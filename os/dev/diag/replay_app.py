"""Dev: launch another Steam app by hand through reaper/Proton like Steam does, with stderr to a file (generalised from replay.py, which is for app 63710).
usage: replay_app.py APPID 'install dir name' 'exe relative to the install dir' [seconds]   env REPLAY_ENV='K=V K=V' adds/overrides variables, REPLAY_ARGS='-a -b' appends game arguments, REPLAY_WRAP=1 puts steam-launch-wrapper in front like Steam."""
import json, os, subprocess, sys, time
appid, dirname, exe_rel = sys.argv[1], sys.argv[2], sys.argv[3]; appid_arg = appid; seconds = int(sys.argv[4]) if len(sys.argv) > 4 else 90
CAPTURE = '/storage/wispos/dev/launch-capture/launch.json'
if os.path.exists(CAPTURE):
    env = dict(e.split('=', 1) for e in json.load(open(CAPTURE))['env'] if '=' in e)
else:       # no capture on this card: start from the running Steam client's own environment (what Steam hands every game) plus the compat variables Steam adds
    pid = subprocess.run(['pidof', 'steam'], capture_output=True, text=True).stdout.split()[0]
    env = dict(e.split(b'=', 1) for e in open(f'/proc/{pid}/environ', 'rb').read().split(b'\0') if b'=' in e)
    env = {k.decode(): v.decode() for k, v in env.items()}
    env.update({'STEAM_COMPAT_CLIENT_INSTALL_PATH': '/storage/wispos/runtime/steam', 'STEAM_COMPAT_LIBRARY_PATHS': '/storage/wispos/runtime/steam/steamapps',
                'STEAM_COMPAT_TOOL_PATHS': '/storage/wispos/runtime/steam/steamapps/common/Proton 11.0 (ARM64)', 'PRESSURE_VESSEL_FILESYSTEMS_RO': '/run/udev', 'STEAM_COMPAT_APP_ID': appid_arg})
steam = '/storage/wispos/runtime/steam'; app = f'{steam}/steamapps'; install = f'{app}/common/{dirname}'
if os.path.exists(CAPTURE):
    for k, v in list(env.items()): env[k] = v.replace('63710', appid).replace('BIT.TRIP RUNNER', dirname)
env.update({'STEAM_COMPAT_INSTALL_PATH': install, 'STEAM_COMPAT_DATA_PATH': f'{app}/compatdata/{appid}', 'SteamAppId': appid, 'SteamGameId': appid,
            'WINEDEBUG': 'err+all,fixme-all', 'PROTON_LOG': '1', 'PROTON_LOG_DIR': f'{app}/compatdata/{appid}', 'PROTON_USE_WINED3D': '1'})
for kv in os.environ.get('REPLAY_ENV', '').split():
    k, _, v = kv.partition('='); env[k] = v
cmd = [f'{steam}/steamrtarm64/reaper', 'SteamLaunch', f'AppId={appid}', '--', f'{app}/common/SteamLinuxRuntime_4-arm64/_v2-entry-point', '--verb=waitforexitandrun', '--',
       f'{app}/common/Proton 11.0 (ARM64)/proton', 'waitforexitandrun', f'{install}/{exe_rel}'] + os.environ.get('REPLAY_ARGS', '').split()
if os.environ.get('REPLAY_WRAP'): cmd = [f'{steam}/linuxarm64/steam-launch-wrapper', '--'] + cmd       # run it the way Steam does (REPLAY_WRAP=1)
out = f'/storage/wispos/dev/replay-app-{appid}.log'; log = open(out, 'wb'); log.write((' '.join(cmd) + '\n').encode()); log.flush()
t0 = time.time(); p = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT, cwd=os.path.dirname(f'{install}/{exe_rel}'))
try: rc = p.wait(timeout=seconds)
except subprocess.TimeoutExpired: p.kill(); rc = f'killed after {seconds}s (still running)'
log.write(f'\n--- exit {rc} after {time.time() - t0:.1f}s\n'.encode()); log.close()
