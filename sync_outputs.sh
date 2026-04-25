#!/bin/bash
echo "Syncing from fsn1..."
rsync -av --progress root@fsn1.noze.ro:/data/kmz_outputs/ /data/kmz_outputs/
echo "Syncing from fsn2..."
rsync -av --progress root@fsn2.noze.ro:/data/kmz_outputs/ /data/kmz_outputs/
echo "Done. Total files:"
ls /data/kmz_outputs/ | wc -l
