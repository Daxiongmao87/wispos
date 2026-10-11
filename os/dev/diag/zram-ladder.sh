#!/bin/sh
W=/storage/wispos/dev
sh $W/zcycle.sh "A3-768" 768 0
sh $W/zcycle.sh "B3-2048-cap450" 2048 450
sh $W/zcycle.sh "A4-768" 768 0
sh $W/zcycle.sh "B4-2048-cap450" 2048 450
echo ZLADDERDONE
