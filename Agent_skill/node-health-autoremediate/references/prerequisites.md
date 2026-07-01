# Node Health Auto-Remediate Prerequisites

To run this skill successfully on physical nodes, verify the following configurations:

## 1. Docker Sudo Permissions
The script executes `sudo -n systemctl restart docker` to restart the Docker engine.
- To prevent prompt blocks, grant passwordless sudo to the SSH user (`noc`):
  ```bash
  # Edit sudoers via visudo
  noc ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart docker
  ```
- The `-n` flag in `sudo -n` guarantees that if permissions are not granted, the command exits with an error instead of hanging waiting for a password prompt.

## 2. Docker Group Membership
For container actions like `docker restart` or `docker logs`, ensure the SSH user is part of the `docker` group:
```bash
sudo usermod -aG docker noc
```

## 3. SSH Configurations
- Ensure public key auth is configured to avoid transmitting passwords via environment variables.
- Copy your public key:
  ```bash
  ssh-copy-id -i ~/.ssh/id_rsa.pub noc@<node-ip>
  ```

## 4. Preventing Flapping (Flapping Safeguard)
If scheduling this checks via cron, implement a cooldown window or count consecutive violations (e.g., 3 failed metrics intervals) before triggering engine restarts to avoid service flapping.
