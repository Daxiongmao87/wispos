#!/bin/sh
S=/storage/wispos/dev/lat.sh
run() { sysctl -w vm.swappiness=$2 vm.page-cluster=$3 >/dev/null; sleep 15; sh $S "$1"; }
run "A baseline"                      180 3
run "B page-cluster 0"                180 0
run "C page-cluster 0 swappiness 100" 100 0
run "A2 baseline again (drift)"       180 3
sysctl -w vm.swappiness=180 vm.page-cluster=3 >/dev/null; echo "restored swappiness=180 page-cluster=3"; echo LADDERDONE
