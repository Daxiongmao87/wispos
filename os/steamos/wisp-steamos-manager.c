/* wisp-steamos-manager: the part of Valve's SteamOS D-Bus contract that WispOS answers (docs/WISPOS-STEAMOS-CONTRACT.md).
 *
 * Steam's UI asks the OS for updates, performance settings, session switches and device formatting over two public D-Bus interfaces. Both may be implemented partially: Steam
 * checks which members exist and hides the settings that are missing, so this program serves exactly what WispOS can really do and nothing is faked.
 *   com.steampowered.Atomupd1                       /com/steampowered/Atomupd1          the OS update interface (CheckForUpdates, StartUpdate, Pause/Resume/Cancel, status, and
 *                                                                                       DisableHttpProxy, which Steam calls at start and which is not in Valve's public file)
 *   com.steampowered.SteamOSManager1.Manager2       /com/steampowered/SteamOSManager1   DeviceModel, ReloadConfig
 *   com.steampowered.SteamOSManager1.CpuScaling1    /com/steampowered/SteamOSManager1   the cpufreq governors (sysfs, read live, written for every policy)
 * Interface definitions are Valve's (the XML files in os/steamos/interfaces, MIT); os/test_steamos_manager.py checks this program's introspection against them member by member.
 *
 * Which bus each one is on was measured on the device (docs/WISPOS-NOTES.md, "Steam's SteamOS D-Bus calls"): Steam starts SteamOSManager1 by activation on its SESSION bus and calls
 * Atomupd1 on the SYSTEM bus. So one binary has two roles and each bus activates its own process:
 *   wisp-steamos-manager --atomupd system      the update interface (run by wisp-steamos-manager.service)
 *   wisp-steamos-manager --manager session     the manager interfaces (D-Bus activation inside Steam's session)
 * Either role may be given on either bus, and both on one bus (the tests do that on a private bus); at least one is required.
 * Environment (test seams and paths; every one has the production default): WISP_UPDATE_HELPER (/usr/lib/wispos/wisp-update), WISP_OS_RELEASE (/etc/os-release),
 * WISP_CPUFREQ_DIR (/sys/devices/system/cpu/cpufreq), WISP_DT_MODEL (/proc/device-tree/model).
 *
 * The update work is done by a helper program, so the network, the hashes and the image writing live in one tested place and this daemon stays small (a few hundred KB resident):
 *   HELPER check          prints one line per update:   update ID VERSION VARIANT SIZE TYPE            (TYPE 0 minor, 1 major)
 *                                                       later  ID VERSION VARIANT SIZE TYPE REQUIRES   (needs a newer base first)
 *                         and exits 0; on failure it prints `error CODE MESSAGE` and exits non-zero. CODE becomes the D-Bus error name (see error_name).
 *   HELPER apply ID       prints `progress PERCENT ETA_UNIX_SECONDS` while it works, `done` when the update is staged, or `error CODE MESSAGE`; exit 0 only after `done`.
 * Pause and resume are SIGSTOP/SIGCONT to the helper's process group, cancel is SIGTERM (the helper removes its partial files). One helper at a time.
 * Signals go to the helper's whole group, so a download in a child process stops with it.
 *
 * Linked against the image's own libsystemd; only the public headers are needed to build (hash-pinned, see os/build-rootfs.sh).
 */
#define _GNU_SOURCE
#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <glob.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/epoll.h>
#include <sys/wait.h>
#include <unistd.h>

#include <systemd/sd-bus.h>
#include <systemd/sd-daemon.h>
#include <systemd/sd-event.h>

enum { UPD_IDLE = 0, UPD_IN_PROGRESS = 1, UPD_PAUSED = 2, UPD_SUCCESSFUL = 3, UPD_FAILED = 4, UPD_CANCELLED = 5 };   /* Atomupd1 UpdateStatus */
enum { JOB_NONE, JOB_CHECK, JOB_APPLY };

typedef struct { char *id, *version, *variant, *requires; uint64_t size; uint32_t type; } Update;
typedef struct { Update *v; size_t n; } UpdateList;

typedef struct {
    int kind; pid_t pid; int fd; sd_event_source *io, *child;
    char line[1024]; size_t len;
    sd_bus_message *pending;               /* CheckForUpdates call waiting for its reply */
    UpdateList avail, later;               /* a check's result, published when the helper ends well */
    bool done_seen, cancel_asked, paused; int exit_status;
    char err_code[128], err_msg[256];
} Job;

typedef struct {
    sd_event *event; sd_bus *atomupd_bus, *manager_bus;
    const char *helper, *os_release, *cpufreq_dir, *dt_model;
    uint32_t status; double progress; uint64_t eta;
    char *update_build_id, *failure_code, *failure_message, *current_version, *current_build_id, *variant;
    UpdateList avail, later;
    Job *job;
} Manager;

#define ATOMUPD_NAME "com.steampowered.Atomupd1"
#define ATOMUPD_PATH "/com/steampowered/Atomupd1"
#define MANAGER_NAME "com.steampowered.SteamOSManager1"
#define MANAGER_PATH "/com/steampowered/SteamOSManager1"
#define KNOWN_VARIANT "stable"
#define PROGRESS_STEP 0.5          /* percent: smaller progress changes are not announced on the bus */

/* ---- small helpers ---- */
static void set_str(char **dst, const char *s) { free(*dst); *dst = strdup(s ? s : ""); }

static char *read_file(const char *path, char *buf, size_t cap) {
    FILE *f = fopen(path, "r"); if (!f) { buf[0] = 0; return NULL; }
    size_t n = fread(buf, 1, cap - 1, f); fclose(f); buf[n] = 0;
    while (n && (buf[n - 1] == '\n' || buf[n - 1] == ' ' || buf[n - 1] == 0)) buf[--n] = 0;
    return buf;
}

static void list_free(UpdateList *l) {
    for (size_t i = 0; i < l->n; i++) { free(l->v[i].id); free(l->v[i].version); free(l->v[i].variant); free(l->v[i].requires); }
    free(l->v); l->v = NULL; l->n = 0;
}

static void list_swap(UpdateList *dst, UpdateList *src) { list_free(dst); *dst = *src; src->v = NULL; src->n = 0; }

/* The value of KEY= in an os-release style file (quotes removed), or "" . */
static void os_release_value(const char *path, const char *key, char *out, size_t cap) {
    out[0] = 0; FILE *f = fopen(path, "r"); if (!f) return;
    char line[512]; size_t kl = strlen(key);
    while (fgets(line, sizeof line, f)) {
        if (strncmp(line, key, kl) || line[kl] != '=') continue;
        char *v = line + kl + 1; size_t n = strlen(v);
        while (n && (v[n - 1] == '\n' || v[n - 1] == '"' || v[n - 1] == '\'')) v[--n] = 0;
        if (*v == '"' || *v == '\'') v++;
        snprintf(out, cap, "%s", v); break;
    }
    fclose(f);
}

/* The running system's identity, from the image's os-release (os/stage_rootfs.py rebrand() writes it): VERSION_ID, and BUILD_ID, which has to compare like a version number
 * (Atomupd1 says "20230906.1"). WISPOS_VERSION is the same version under WispOS's own key and is read first, so an older image that only has that one still answers. */
static void load_identity(Manager *m) {
    char v[128], b[128];
    os_release_value(m->os_release, "WISPOS_VERSION", v, sizeof v); if (!v[0]) os_release_value(m->os_release, "VERSION_ID", v, sizeof v);
    os_release_value(m->os_release, "BUILD_ID", b, sizeof b);
    set_str(&m->current_version, v[0] ? v : "unknown"); set_str(&m->current_build_id, b[0] ? b : (v[0] ? v : "unknown"));
}

static void changed(Manager *m, const char *p1, const char *p2, const char *p3, const char *p4) {
    const char *all[4] = {p1, p2, p3, p4}; char *names[5]; int n = 0;
    for (int i = 0; i < 4; i++) if (all[i]) names[n++] = (char *)all[i];
    names[n] = NULL; if (n && m->atomupd_bus) sd_bus_emit_properties_changed_strv(m->atomupd_bus, ATOMUPD_PATH, "com.steampowered.Atomupd1", names);
}

/* ---- Atomupd1: property getters ---- */
static int append_updates(sd_bus_message *r, const UpdateList *l, bool later) {
    int rc = sd_bus_message_open_container(r, 'a', "{sa{sv}}"); if (rc < 0) return rc;
    for (size_t i = 0; i < l->n; i++) {
        const Update *u = &l->v[i];
        if ((rc = sd_bus_message_open_container(r, 'e', "sa{sv}")) < 0 || (rc = sd_bus_message_append_basic(r, 's', u->id)) < 0 || (rc = sd_bus_message_open_container(r, 'a', "{sv}")) < 0) return rc;
        rc = sd_bus_message_append(r, "{sv}{sv}{sv}{sv}", "variant", "s", u->variant, "version", "s", u->version, "estimated_size", "t", u->size, "update_type", "u", u->type);
        if (rc < 0) return rc;
        if (later && (rc = sd_bus_message_append(r, "{sv}", "requires", "s", u->requires ? u->requires : "")) < 0) return rc;
        if ((rc = sd_bus_message_close_container(r)) < 0 || (rc = sd_bus_message_close_container(r)) < 0) return rc;
    }
    return sd_bus_message_close_container(r);
}

#define STR_PROP(fn, field) static int fn(sd_bus *b, const char *p, const char *i, const char *n, sd_bus_message *r, void *ud, sd_bus_error *e) { \
    (void)b; (void)p; (void)i; (void)n; (void)e; Manager *m = ud; return sd_bus_message_append(r, "s", m->field ? m->field : ""); }
STR_PROP(prop_update_build_id, update_build_id) STR_PROP(prop_variant, variant) STR_PROP(prop_failure_code, failure_code) STR_PROP(prop_failure_message, failure_message)
STR_PROP(prop_current_version, current_version) STR_PROP(prop_current_build_id, current_build_id)

static int prop_version(sd_bus *b, const char *p, const char *i, const char *n, sd_bus_message *r, void *ud, sd_bus_error *e) { (void)b; (void)p; (void)i; (void)n; (void)ud; (void)e; return sd_bus_message_append(r, "u", (uint32_t)1); }
static int prop_status(sd_bus *b, const char *p, const char *i, const char *n, sd_bus_message *r, void *ud, sd_bus_error *e) { (void)b; (void)p; (void)i; (void)n; (void)e; return sd_bus_message_append(r, "u", ((Manager *)ud)->status); }
static int prop_progress(sd_bus *b, const char *p, const char *i, const char *n, sd_bus_message *r, void *ud, sd_bus_error *e) { (void)b; (void)p; (void)i; (void)n; (void)e; return sd_bus_message_append(r, "d", ((Manager *)ud)->progress); }
static int prop_eta(sd_bus *b, const char *p, const char *i, const char *n, sd_bus_message *r, void *ud, sd_bus_error *e) { (void)b; (void)p; (void)i; (void)n; (void)e; return sd_bus_message_append(r, "t", ((Manager *)ud)->eta); }
static int prop_known_variants(sd_bus *b, const char *p, const char *i, const char *n, sd_bus_message *r, void *ud, sd_bus_error *e) { (void)b; (void)p; (void)i; (void)n; (void)ud; (void)e; return sd_bus_message_append(r, "as", 1, KNOWN_VARIANT); }
static int prop_avail(sd_bus *b, const char *p, const char *i, const char *n, sd_bus_message *r, void *ud, sd_bus_error *e) { (void)b; (void)p; (void)i; (void)n; (void)e; return append_updates(r, &((Manager *)ud)->avail, false); }
static int prop_later(sd_bus *b, const char *p, const char *i, const char *n, sd_bus_message *r, void *ud, sd_bus_error *e) { (void)b; (void)p; (void)i; (void)n; (void)e; return append_updates(r, &((Manager *)ud)->later, true); }

/* ---- the helper process ---- */
static void job_free(Manager *m) {
    Job *j = m->job; if (!j) return;
    sd_event_source_unref(j->io); sd_event_source_unref(j->child);
    if (j->fd >= 0) close(j->fd);
    if (j->pending) sd_bus_message_unref(j->pending);
    list_free(&j->avail); list_free(&j->later); free(j); m->job = NULL;
}

static bool add_update(UpdateList *l, char **f, int nf, bool later) {
    if (nf < (later ? 7 : 6)) return false;
    Update *v = realloc(l->v, (l->n + 1) * sizeof *v); if (!v) return false;
    l->v = v; Update *u = &v[l->n++];
    u->id = strdup(f[1]); u->version = strdup(f[2]); u->variant = strdup(f[3]); u->size = strtoull(f[4], NULL, 10); u->type = (uint32_t)strtoul(f[5], NULL, 10) ? 1 : 0; u->requires = later ? strdup(f[6]) : NULL;
    return true;
}

/* Atomupd1's FailureCode (and a failed check's error) is a D-Bus error name. The helper's CODE is used as it is when it already is one (it contains a dot and only name characters);
 * a bare token such as DISK_FULL becomes org.wispos.UpdateError.DISK_FULL; anything else is the generic org.freedesktop.DBus.Error.Failed. */
static void error_name(const char *code, char *out, size_t cap) {
    bool ok = code[0] != 0 && code[0] != '.' && strlen(code) < 120;
    for (const char *c = code; *c && ok; c++) ok = isalnum((unsigned char)*c) || *c == '_' || *c == '.';
    if (!ok) snprintf(out, cap, "org.freedesktop.DBus.Error.Failed");
    else if (strchr(code, '.')) snprintf(out, cap, "%s", code);
    else snprintf(out, cap, "org.wispos.UpdateError.%s", code);
}

static void set_status(Manager *m, uint32_t s) { if (m->status != s) { m->status = s; changed(m, "UpdateStatus", NULL, NULL, NULL); } }

static void on_helper_line(Manager *m, char *line) {
    Job *j = m->job; char *f[8]; int nf = 0;
    if (!strncmp(line, "error ", 6)) {                                   /* error CODE MESSAGE...: the message is the rest of the line, as written */
        char *code = line + 6; char *sp = strchr(code, ' ');
        if (sp) *sp++ = 0;
        snprintf(j->err_code, sizeof j->err_code, "%s", code); snprintf(j->err_msg, sizeof j->err_msg, "%s", sp ? sp : "");
        return;
    }
    for (char *t = strtok(line, " "); t && nf < 8; t = strtok(NULL, " ")) f[nf++] = t;
    if (!nf) return;
    if (j->kind == JOB_CHECK) {
        if (!strcmp(f[0], "update")) add_update(&j->avail, f, nf, false); else if (!strcmp(f[0], "later")) add_update(&j->later, f, nf, true);
    } else if (!strcmp(f[0], "progress") && nf >= 2) {
        double pct = atof(f[1]); if (pct < 0) pct = 0; if (pct > 100) pct = 100;
        uint64_t eta = nf >= 3 ? strtoull(f[2], NULL, 10) : 0; bool pc = pct - m->progress >= PROGRESS_STEP || pct < m->progress, ec = eta != m->eta;
        m->progress = pct; m->eta = eta;
        if (pc || ec) changed(m, pc ? "ProgressPercentage" : NULL, ec ? "EstimatedCompletionTime" : NULL, NULL, NULL);
    } else if (!strcmp(f[0], "done")) j->done_seen = true;
}

static void drain_helper(Manager *m) {
    Job *j = m->job; char c[512]; ssize_t n;
    while (j->fd >= 0 && (n = read(j->fd, c, sizeof c)) > 0) {
        for (ssize_t i = 0; i < n; i++) {
            if (c[i] == '\n') { j->line[j->len] = 0; on_helper_line(m, j->line); j->len = 0; }
            else if (j->len + 1 < sizeof j->line) j->line[j->len++] = c[i];
        }
    }
}

static void finish_check(Manager *m, int exit_ok) {
    Job *j = m->job; sd_bus_message *call = j->pending, *reply = NULL;
    if (exit_ok) {
        list_swap(&m->avail, &j->avail); list_swap(&m->later, &j->later);
        sd_bus_emit_properties_changed(m->atomupd_bus, ATOMUPD_PATH, "com.steampowered.Atomupd1", "UpdatesAvailable", "UpdatesAvailableLater", NULL);
        if (sd_bus_message_new_method_return(call, &reply) >= 0 && append_updates(reply, &m->avail, false) >= 0 && append_updates(reply, &m->later, true) >= 0) sd_bus_send(m->atomupd_bus, reply, NULL);
    } else {
        sd_bus_error err = SD_BUS_ERROR_NULL; char name[160]; error_name(j->err_code, name, sizeof name);
        sd_bus_error_set(&err, name, j->err_msg[0] ? j->err_msg : j->exit_status == 127 ? "the update helper could not be run" : "the update check failed");
        sd_bus_reply_method_error(call, &err); sd_bus_error_free(&err);
    }
    if (reply) sd_bus_message_unref(reply);
}

static void finish_apply(Manager *m, int exit_ok) {
    Job *j = m->job;
    if (exit_ok && j->done_seen) { m->progress = 100; changed(m, "ProgressPercentage", NULL, NULL, NULL); set_status(m, UPD_SUCCESSFUL); return; }
    if (j->cancel_asked) { set_status(m, UPD_CANCELLED); return; }
    char name[160]; error_name(j->err_code, name, sizeof name); set_str(&m->failure_code, name);
    set_str(&m->failure_message, j->err_msg[0] ? j->err_msg : j->exit_status == 127 ? "the update helper could not be run" : "the update helper stopped before the update was staged");
    changed(m, "FailureCode", "FailureMessage", NULL, NULL); set_status(m, UPD_FAILED);
}

static int on_helper_io(sd_event_source *s, int fd, uint32_t revents, void *ud) { (void)s; (void)fd; (void)revents; if (((Manager *)ud)->job) drain_helper(ud); return 0; }

static int on_helper_exit(sd_event_source *s, const siginfo_t *si, void *ud) {
    (void)s; Manager *m = ud; Job *j = m->job; if (!j) return 0;
    drain_helper(m);
    int ok = si->si_code == CLD_EXITED && si->si_status == 0; j->exit_status = si->si_code == CLD_EXITED ? si->si_status : -1;
    if (j->kind == JOB_CHECK) finish_check(m, ok); else finish_apply(m, ok);
    job_free(m); return 0;
}

/* Start HELPER with ARG1 [ARG2]; stdout is read here, stderr goes to the journal. Returns 0 or a negative errno. */
static int job_start(Manager *m, int kind, const char *arg1, const char *arg2) {
    int pipefd[2]; if (pipe2(pipefd, O_CLOEXEC) < 0) return -errno;
    pid_t pid = fork(); if (pid < 0) { int e = -errno; close(pipefd[0]); close(pipefd[1]); return e; }
    if (pid == 0) {
        setpgid(0, 0); dup2(pipefd[1], 1); sigset_t all; sigemptyset(&all); sigprocmask(SIG_SETMASK, &all, NULL);
        execl(m->helper, m->helper, arg1, arg2, (char *)NULL); dprintf(2, "wisp-steamos-manager: cannot run %s: %s\n", m->helper, strerror(errno)); _exit(127);
    }
    setpgid(pid, pid); close(pipefd[1]); fcntl(pipefd[0], F_SETFL, O_NONBLOCK);
    Job *j = calloc(1, sizeof *j); j->kind = kind; j->pid = pid; j->fd = pipefd[0]; m->job = j;
    sd_event_add_io(m->event, &j->io, j->fd, EPOLLIN, on_helper_io, m);
    sd_event_add_child(m->event, &j->child, pid, WEXITED, on_helper_exit, m);
    return 0;
}

/* ---- Atomupd1: methods ---- */
static int busy_error(sd_bus_error *e) { return sd_bus_error_set(e, "org.freedesktop.DBus.Error.Failed", "an update is already being applied"); }

static int method_check(sd_bus_message *msg, void *ud, sd_bus_error *e) {
    Manager *m = ud;
    if (m->job && m->job->kind == JOB_APPLY) {                     /* the UI may ask while an update downloads: answer from the last check, do not disturb the helper */
        sd_bus_message *reply = NULL; int r = sd_bus_message_new_method_return(msg, &reply);
        if (r >= 0 && (r = append_updates(reply, &m->avail, false)) >= 0 && (r = append_updates(reply, &m->later, true)) >= 0) r = sd_bus_send(m->atomupd_bus, reply, NULL);
        sd_bus_message_unref(reply); (void)e; return r < 0 ? r : 1;
    }
    if (m->job) return sd_bus_error_set(e, "org.freedesktop.DBus.Error.Failed", "an update check is already running");
    int rc = job_start(m, JOB_CHECK, "check", NULL); if (rc < 0) return sd_bus_error_set_errno(e, rc);
    m->job->pending = sd_bus_message_ref(msg);
    return 1;                                                      /* the reply is sent when the helper ends */
}

static bool known_update(const Manager *m, const char *id) { for (size_t i = 0; i < m->avail.n; i++) if (!strcmp(m->avail.v[i].id, id)) return true; return false; }

static int method_start(sd_bus_message *msg, void *ud, sd_bus_error *e) {
    Manager *m = ud; const char *id; int rc = sd_bus_message_read(msg, "s", &id); if (rc < 0) return rc;
    if (m->job || m->status == UPD_IN_PROGRESS || m->status == UPD_PAUSED) return busy_error(e);
    if (!known_update(m, id)) return sd_bus_error_set(e, "org.freedesktop.DBus.Error.InvalidArgs", "that update is not available: call CheckForUpdates first and pick an id it returned");
    if ((rc = job_start(m, JOB_APPLY, "apply", id)) < 0) return sd_bus_error_set_errno(e, rc);
    m->progress = 0; m->eta = 0; set_str(&m->failure_code, ""); set_str(&m->failure_message, ""); set_str(&m->update_build_id, id);
    changed(m, "ProgressPercentage", "EstimatedCompletionTime", "FailureCode", "FailureMessage"); sd_bus_emit_properties_changed(m->atomupd_bus, ATOMUPD_PATH, "com.steampowered.Atomupd1", "UpdateBuildID", NULL);
    set_status(m, UPD_IN_PROGRESS);
    return sd_bus_reply_method_return(msg, NULL);
}

static int method_pause(sd_bus_message *msg, void *ud, sd_bus_error *e) {
    Manager *m = ud; if (!m->job || m->job->kind != JOB_APPLY || m->status != UPD_IN_PROGRESS) return sd_bus_reply_method_return(msg, NULL);    /* "if any" */
    (void)e; kill(-m->job->pid, SIGSTOP); m->job->paused = true; set_status(m, UPD_PAUSED); return sd_bus_reply_method_return(msg, NULL);
}

static int method_resume(sd_bus_message *msg, void *ud, sd_bus_error *e) {
    Manager *m = ud; if (!m->job || m->status != UPD_PAUSED) return sd_bus_error_set(e, "org.freedesktop.DBus.Error.Failed", "the update is not paused");
    kill(-m->job->pid, SIGCONT); m->job->paused = false; set_status(m, UPD_IN_PROGRESS); return sd_bus_reply_method_return(msg, NULL);
}

static int method_cancel(sd_bus_message *msg, void *ud, sd_bus_error *e) {
    Manager *m = ud; (void)e; if (!m->job || m->job->kind != JOB_APPLY) return sd_bus_reply_method_return(msg, NULL);
    m->job->cancel_asked = true; kill(-m->job->pid, SIGTERM); if (m->job->paused) kill(-m->job->pid, SIGCONT);       /* a stopped process only sees SIGTERM once it runs */
    return sd_bus_reply_method_return(msg, NULL);
}

static int method_switch_variant(sd_bus_message *msg, void *ud, sd_bus_error *e) {
    (void)ud; const char *v; int rc = sd_bus_message_read(msg, "s", &v); if (rc < 0) return rc;
    if (strcmp(v, KNOWN_VARIANT)) return sd_bus_error_set(e, "org.freedesktop.DBus.Error.InvalidArgs", "WispOS has one update variant: stable");
    return sd_bus_reply_method_return(msg, NULL);
}

/* Steam calls this once at start-up to apply its proxy setting (seen on the device). The update helper never uses a proxy, so "disabled" is already the truth. */
static int method_disable_proxy(sd_bus_message *msg, void *ud, sd_bus_error *e) { (void)ud; (void)e; return sd_bus_reply_method_return(msg, NULL); }

static const sd_bus_vtable atomupd_vtable[] = {
    SD_BUS_VTABLE_START(0),
    SD_BUS_PROPERTY("Version", "u", prop_version, 0, SD_BUS_VTABLE_PROPERTY_CONST),
    SD_BUS_PROPERTY("ProgressPercentage", "d", prop_progress, 0, SD_BUS_VTABLE_PROPERTY_EMITS_CHANGE),
    SD_BUS_PROPERTY("EstimatedCompletionTime", "t", prop_eta, 0, SD_BUS_VTABLE_PROPERTY_EMITS_CHANGE),
    SD_BUS_PROPERTY("UpdateStatus", "u", prop_status, 0, SD_BUS_VTABLE_PROPERTY_EMITS_CHANGE),
    SD_BUS_PROPERTY("UpdateBuildID", "s", prop_update_build_id, 0, SD_BUS_VTABLE_PROPERTY_EMITS_CHANGE),
    SD_BUS_PROPERTY("Variant", "s", prop_variant, 0, SD_BUS_VTABLE_PROPERTY_EMITS_CHANGE),
    SD_BUS_PROPERTY("FailureCode", "s", prop_failure_code, 0, SD_BUS_VTABLE_PROPERTY_EMITS_CHANGE),
    SD_BUS_PROPERTY("FailureMessage", "s", prop_failure_message, 0, SD_BUS_VTABLE_PROPERTY_EMITS_CHANGE),
    SD_BUS_PROPERTY("UpdatesAvailable", "a{sa{sv}}", prop_avail, 0, SD_BUS_VTABLE_PROPERTY_EMITS_CHANGE),
    SD_BUS_PROPERTY("UpdatesAvailableLater", "a{sa{sv}}", prop_later, 0, SD_BUS_VTABLE_PROPERTY_EMITS_CHANGE),
    SD_BUS_PROPERTY("CurrentVersion", "s", prop_current_version, 0, SD_BUS_VTABLE_PROPERTY_CONST),
    SD_BUS_PROPERTY("CurrentBuildID", "s", prop_current_build_id, 0, SD_BUS_VTABLE_PROPERTY_CONST),
    SD_BUS_PROPERTY("KnownVariants", "as", prop_known_variants, 0, SD_BUS_VTABLE_PROPERTY_CONST),
    SD_BUS_METHOD_WITH_ARGS("SwitchToVariant", SD_BUS_ARGS("s", variant), SD_BUS_NO_RESULT, method_switch_variant, 0),
    SD_BUS_METHOD_WITH_ARGS("CheckForUpdates", SD_BUS_ARGS("a{sv}", options), SD_BUS_RESULT("a{sa{sv}}", updates_available, "a{sa{sv}}", updates_available_later), method_check, 0),
    SD_BUS_METHOD_WITH_ARGS("StartUpdate", SD_BUS_ARGS("s", id), SD_BUS_NO_RESULT, method_start, 0),
    SD_BUS_METHOD("PauseUpdate", NULL, NULL, method_pause, 0),
    SD_BUS_METHOD("ResumeUpdate", NULL, NULL, method_resume, 0),
    SD_BUS_METHOD("CancelUpdate", NULL, NULL, method_cancel, 0),
    SD_BUS_METHOD("DisableHttpProxy", NULL, NULL, method_disable_proxy, 0),
    SD_BUS_VTABLE_END
};

/* ---- SteamOSManager1: Manager2 and CpuScaling1 ---- */
static int manager_reload(sd_bus_message *msg, void *ud, sd_bus_error *e) { (void)ud; (void)e; return sd_bus_reply_method_return(msg, NULL); }     /* no configuration files yet */

static int prop_device_model(sd_bus *b, const char *p, const char *i, const char *n, sd_bus_message *r, void *ud, sd_bus_error *e) {
    (void)b; (void)p; (void)i; (void)n; (void)e; Manager *m = ud; char model[128];
    if (!read_file(m->dt_model, model, sizeof model) || !model[0]) snprintf(model, sizeof model, "unknown");
    return sd_bus_message_append(r, "(ss)", model, "unknown");      /* (model, variant): the board's own name from the device tree; WispOS has no hardware variants to tell apart */
}

static const sd_bus_vtable manager2_vtable[] = {
    SD_BUS_VTABLE_START(0),
    SD_BUS_METHOD("ReloadConfig", NULL, NULL, manager_reload, 0),
    SD_BUS_PROPERTY("DeviceModel", "(ss)", prop_device_model, 0, SD_BUS_VTABLE_PROPERTY_CONST),
    SD_BUS_VTABLE_END
};

/* cpufreq: the governors every policy offers (the intersection, in the first policy's order) and the one in use (the first policy's, written to all of them). */
static int policies(Manager *m, glob_t *g) {
    char pat[512]; snprintf(pat, sizeof pat, "%s/policy*/scaling_governor", m->cpufreq_dir);
    return glob(pat, 0, NULL, g) == 0 && g->gl_pathc > 0 ? 0 : -ENOENT;
}

static int prop_governors(sd_bus *b, const char *p, const char *i, const char *n, sd_bus_message *r, void *ud, sd_bus_error *e) {
    (void)b; (void)p; (void)i; (void)n; (void)e; glob_t g; int rc;
    if (policies(ud, &g) < 0) return sd_bus_message_append(r, "as", 0);
    char path[600], list[512]; snprintf(path, sizeof path, "%s", g.gl_pathv[0]); strcpy(strrchr(path, '/'), "/scaling_available_governors");
    read_file(path, list, sizeof list);
    rc = sd_bus_message_open_container(r, 'a', "s");
    for (char *save, *t = strtok_r(list, " ", &save); rc >= 0 && t; t = strtok_r(NULL, " ", &save)) rc = sd_bus_message_append_basic(r, 's', t);
    if (rc >= 0) rc = sd_bus_message_close_container(r);
    globfree(&g); return rc;
}

static int prop_governor_get(sd_bus *b, const char *p, const char *i, const char *n, sd_bus_message *r, void *ud, sd_bus_error *e) {
    (void)b; (void)p; (void)i; (void)n; (void)e; glob_t g; char cur[64] = "";
    if (policies(ud, &g) == 0) { read_file(g.gl_pathv[0], cur, sizeof cur); globfree(&g); }
    return sd_bus_message_append(r, "s", cur);
}

static int prop_governor_set(sd_bus *b, const char *p, const char *i, const char *n, sd_bus_message *v, void *ud, sd_bus_error *e) {
    (void)p; (void)i; (void)n; const char *want; int rc = sd_bus_message_read(v, "s", &want); if (rc < 0) return rc;
    glob_t g; if (policies(ud, &g) < 0) return sd_bus_error_set(e, "org.freedesktop.DBus.Error.NotSupported", "this system has no cpufreq policy");
    char path[600], list[512]; snprintf(path, sizeof path, "%s", g.gl_pathv[0]); strcpy(strrchr(path, '/'), "/scaling_available_governors"); read_file(path, list, sizeof list);
    bool ok = false; for (char *save, *t = strtok_r(list, " ", &save); t; t = strtok_r(NULL, " ", &save)) if (!strcmp(t, want)) ok = true;
    if (!ok) { globfree(&g); return sd_bus_error_set(e, "org.freedesktop.DBus.Error.InvalidArgs", "that governor is not in AvailableCpuScalingGovernors"); }
    for (size_t k = 0; k < g.gl_pathc; k++) {
        FILE *f = fopen(g.gl_pathv[k], "w"); if (!f) { rc = sd_bus_error_set_errno(e, -errno); break; }
        if (fprintf(f, "%s\n", want) < 0 || fclose(f) != 0) { rc = sd_bus_error_set_errno(e, -errno); break; }
    }
    globfree(&g);
    if (rc >= 0) sd_bus_emit_properties_changed(b, MANAGER_PATH, "com.steampowered.SteamOSManager1.CpuScaling1", "CpuScalingGovernor", NULL);
    return rc < 0 ? rc : 1;
}

static const sd_bus_vtable cpu_scaling_vtable[] = {
    SD_BUS_VTABLE_START(0),
    SD_BUS_PROPERTY("AvailableCpuScalingGovernors", "as", prop_governors, 0, SD_BUS_VTABLE_PROPERTY_EMITS_CHANGE),
    SD_BUS_WRITABLE_PROPERTY("CpuScalingGovernor", "s", prop_governor_get, prop_governor_set, 0, SD_BUS_VTABLE_PROPERTY_EMITS_CHANGE),
    SD_BUS_VTABLE_END
};

/* ---- start-up ---- */
static int open_bus(const char *which, sd_bus **out) { return !strcmp(which, "session") ? sd_bus_open_user(out) : sd_bus_open_system(out); }

int main(int argc, char **argv) {
    Manager m; memset(&m, 0, sizeof m);
    const char *atomupd_which = NULL, *manager_which = NULL;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--atomupd") && i + 1 < argc) atomupd_which = argv[++i];
        else if (!strcmp(argv[i], "--manager") && i + 1 < argc) manager_which = argv[++i];
        else { atomupd_which = manager_which = NULL; break; }
    }
    bool bad = (!atomupd_which && !manager_which) || (atomupd_which && strcmp(atomupd_which, "system") && strcmp(atomupd_which, "session")) || (manager_which && strcmp(manager_which, "system") && strcmp(manager_which, "session"));
    if (bad) { fprintf(stderr, "usage: %s [--atomupd system|session] [--manager system|session]   (at least one)\n", argv[0]); return 2; }
    m.helper = getenv("WISP_UPDATE_HELPER") ? getenv("WISP_UPDATE_HELPER") : "/usr/lib/wispos/wisp-update";
    m.os_release = getenv("WISP_OS_RELEASE") ? getenv("WISP_OS_RELEASE") : "/etc/os-release";
    m.cpufreq_dir = getenv("WISP_CPUFREQ_DIR") ? getenv("WISP_CPUFREQ_DIR") : "/sys/devices/system/cpu/cpufreq";
    m.dt_model = getenv("WISP_DT_MODEL") ? getenv("WISP_DT_MODEL") : "/proc/device-tree/model";
    load_identity(&m); set_str(&m.variant, KNOWN_VARIANT); set_str(&m.update_build_id, ""); set_str(&m.failure_code, ""); set_str(&m.failure_message, "");
    signal(SIGPIPE, SIG_IGN);

    int rc = sd_event_default(&m.event); if (rc < 0) { fprintf(stderr, "event loop: %s\n", strerror(-rc)); return 1; }
    sigset_t ss; sigemptyset(&ss); sigaddset(&ss, SIGTERM); sigaddset(&ss, SIGINT); sigaddset(&ss, SIGCHLD);        /* sd-event wants SIGCHLD blocked for sd_event_add_child */
    sigprocmask(SIG_BLOCK, &ss, NULL);
    sd_event_add_signal(m.event, NULL, SIGTERM, NULL, NULL); sd_event_add_signal(m.event, NULL, SIGINT, NULL, NULL);

    if (atomupd_which && (rc = open_bus(atomupd_which, &m.atomupd_bus)) < 0) { fprintf(stderr, "cannot connect to the %s bus: %s\n", atomupd_which, strerror(-rc)); return 1; }
    if (manager_which) {
        if (atomupd_which && !strcmp(atomupd_which, manager_which)) m.manager_bus = sd_bus_ref(m.atomupd_bus);
        else if ((rc = open_bus(manager_which, &m.manager_bus)) < 0) { fprintf(stderr, "cannot connect to the %s bus: %s\n", manager_which, strerror(-rc)); return 1; }
    }
    const char *step = NULL;
#define STEP(label, call) do { if (!step && (rc = (call)) < 0) step = label; } while (0)
    /* Steam asks the manager for org.freedesktop.DBus.ObjectManager.GetManagedObjects at path "/" right after it finds the name (measured on the device, docs/WISPOS-NOTES.md); an
     * "UnknownObject" there makes it log "failed to connect to OS service" and drop the manager. It goes first, so the objects registered below it are announced and listed. */
    if (m.manager_bus) STEP("ObjectManager at /", sd_bus_add_object_manager(m.manager_bus, NULL, "/"));
    if (m.atomupd_bus) STEP("Atomupd1 object", sd_bus_add_object_vtable(m.atomupd_bus, NULL, ATOMUPD_PATH, "com.steampowered.Atomupd1", atomupd_vtable, &m));
    if (m.manager_bus) STEP("Manager2 object", sd_bus_add_object_vtable(m.manager_bus, NULL, MANAGER_PATH, "com.steampowered.SteamOSManager1.Manager2", manager2_vtable, &m));
    if (m.manager_bus) STEP("CpuScaling1 object", sd_bus_add_object_vtable(m.manager_bus, NULL, MANAGER_PATH, "com.steampowered.SteamOSManager1.CpuScaling1", cpu_scaling_vtable, &m));
    if (m.atomupd_bus) STEP("name " ATOMUPD_NAME, sd_bus_request_name(m.atomupd_bus, ATOMUPD_NAME, 0));
    if (m.manager_bus) STEP("name " MANAGER_NAME, sd_bus_request_name(m.manager_bus, MANAGER_NAME, 0));
#undef STEP
    if (step) { fprintf(stderr, "cannot register on the bus (%s): %s\n", step, strerror(-rc)); return 1; }
    if (m.atomupd_bus) sd_bus_attach_event(m.atomupd_bus, m.event, 0);
    if (m.manager_bus && m.manager_bus != m.atomupd_bus) sd_bus_attach_event(m.manager_bus, m.event, 0);
    /* Steam's session bus ends with every Steam restart: the session-activated process must end with it, or each restart would leave one behind. */
    if (m.atomupd_bus) sd_bus_set_exit_on_disconnect(m.atomupd_bus, 1);
    if (m.manager_bus) sd_bus_set_exit_on_disconnect(m.manager_bus, 1);

    sd_notify(0, "READY=1");
    rc = sd_event_loop(m.event);
    if (m.job) { kill(-m.job->pid, SIGTERM); kill(-m.job->pid, SIGCONT); }
    return rc < 0 ? 1 : 0;
}
