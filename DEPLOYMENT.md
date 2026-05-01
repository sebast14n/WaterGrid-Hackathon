# Deployment Documentation

## Overview
This document outlines the comprehensive architecture for a distributed infrastructure setup for the WaterGrid Hackathon project. The setup involves a master node and multiple worker nodes to efficiently manage deployment tasks.

## Architecture
- **Master Node**: VPS2
- **Worker Nodes**: FSN1, FSN2

## 1. SSH Key Distribution
To enable secure communication between nodes, SSH keys must be distributed as follows:
- Generate an SSH key pair on the master node (if not already present):  
  ```bash
  ssh-keygen -t rsa -b 2048
  ```  
- Copy the public key to both worker nodes:  
  ```bash
  ssh-copy-id user@FSN1
  ssh-copy-id user@FSN2
  ```  
- Verify access by SSHing into the worker nodes:  
  ```bash
  ssh user@FSN1
  ssh user@FSN2
  ```

## 2. Docker Image Building on Workers
The workers will be responsible for building Docker images:
- Ensure Docker is installed on both worker nodes.  
- From the master node, trigger the build process on each worker:
  ```bash
  ssh user@FSN1 'docker build -t watergrid-image /path/to/dockerfile'
  ssh user@FSN2 'docker build -t watergrid-image /path/to/dockerfile'
  ```

## 3. Nginx Reverse Proxy Configuration
To manage incoming traffic, configure Nginx on the master node:
- Install Nginx:  
  ```bash
  sudo apt update
  sudo apt install nginx
  ```  
- Configure Nginx to reverse proxy requests to worker nodes:  
  ```nginx
  server {
      listen 80;
      server_name your_domain.com;

      location / {
          proxy_pass http://FSN1:port;
          proxy_pass http://FSN2:port;
      }
  }
  ```
- Test and restart Nginx:  
  ```bash
  sudo nginx -t
  sudo systemctl restart nginx
  ```

## 4. Redis Task Distribution
Redis will be used for task distribution:
- Install Redis on the master node:  
  ```bash
  sudo apt install redis-server
  ```
- Configure Redis to allow remote connections by editing `/etc/redis/redis.conf` and changing:
  ```text
  bind 0.0.0.0
  ```
- Ensure Redis service is running:  
  ```bash
  sudo systemctl start redis
  ```  
- From workers, connect to the Redis instance running on the master node:
  ```bash
  redis-cli -h VPS2
  ```

## 5. Result Synchronization
To synchronize results:
- Use a shared directory or service like Rsync to synchronize files from workers back to master node:
  ```bash
  rsync -avz user@FSN1:/path/to/results/ /local/path/to/store/results/
  rsync -avz user@FSN2:/path/to/results/ /local/path/to/store/results/
  ```

## Conclusion
This setup ensures a robust distributed infrastructure for the WaterGrid Hackathon, facilitating efficient resource management and task distribution across a master-worker architecture.