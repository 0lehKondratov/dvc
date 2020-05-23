#!/usr/bin/env bash

set -e
set -x

# run azurite
sudo docker run -d --restart always -e executable=blob -p 10000:10000 \
  --tmpfs /opt/azurite/folder \
  mcr.microsoft.com/azure-storage/azurite:3.3.0-preview \
  azurite -l /data --blobHost 0.0.0.0

# save secrets
echo "export AZURE_STORAGE_CONTAINER_NAME='travis-tests'" >>env.sh
echo "export AZURE_STORAGE_CONNECTION_STRING='DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;'" >>env.sh
