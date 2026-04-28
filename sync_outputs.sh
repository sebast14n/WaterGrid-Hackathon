#!/bin/bash
rsync -avz --progress root@fsn1.noze.ro:/data/kmz_outputs/ /data/kmz_outputs/
rsync -avz --progress root@fsn2.noze.ro:/data/kmz_outputs/ /data/kmz_outputs/
