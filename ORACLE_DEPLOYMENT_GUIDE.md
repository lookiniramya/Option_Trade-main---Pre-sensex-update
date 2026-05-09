# OptionsIQ — Complete Oracle Cloud Deployment Guide

After this setup your app runs 24/7 on Oracle's free server.
Phone, laptop, or any browser — open it anywhere, anytime.
No laptop required. Fully free hosting.

---

## What you need before starting

- [ ] Oracle Cloud account (free) — create at cloud.oracle.com
- [ ] Your GitHub repo URL: https://github.com/lookiniramya/trade-helper-app
- [ ] Your Anthropic API key (from console.anthropic.com)
- [ ] Your app password
- [ ] 30 minutes

---

## PART 1 — Create Oracle Cloud account

1. Go to **https://cloud.oracle.com**
2. Click **"Start for free"**
3. Fill in name, email, mobile number
4. Verify mobile via OTP
5. Add credit card — you will NOT be charged, it is for identity only
6. **IMPORTANT:** When asked for Home Region, select **"India West (Mumbai)"**
   This cannot be changed later.
7. Complete signup — takes 2-5 minutes to activate

---

## PART 2 — Create the Virtual Machine

1. Log into Oracle Cloud console at **https://cloud.oracle.com**

2. In the top search bar type **"Instances"** → click **"Instances"** under Compute

3. Click **"Create Instance"**

4. Fill in these fields:

   **Name:** `optionsiq`

   **Image:** Click "Change image" → select **"Ubuntu"** → select **"Ubuntu 22.04"** → click "Select image"

   **Shape:** Click "Change shape"
   - Select **"Ampere"** (ARM-based, free tier)
   - Select **"VM.Standard.A1.Flex"**
   - Set **OCPUs: 2**
   - Set **Memory: 12 GB**
   - Click "Select shape"

   **SSH Keys:** Click "Generate a key pair for me"
   - Click **"Save private key"** — this downloads a file like `ssh-key-2026-05-01.key`
   - Save this file to your laptop. You need it to connect.

5. Leave everything else as default

6. Click **"Create"** at the bottom

7. Wait 2 minutes until the status shows **"Running"**

8. **Copy the Public IP address** shown on the instance page. It looks like `140.238.xxx.xxx`

---

## PART 3 — Open firewall ports (Oracle Cloud Security)

Oracle has its own cloud-level firewall separate from the VM. You must open ports here too.

1. On your instance page, scroll down to **"Instance information"**

2. Under **"Primary VNIC"**, click the **Subnet link** (looks like "subnet-20241201-xxxx")

3. Click **"Security Lists"** → click the security list name

4. Click **"Add Ingress Rules"**

5. Add this rule:
   - Source Type: **CIDR**
   - Source CIDR: **0.0.0.0/0**
   - IP Protocol: **TCP**
   - Destination Port Range: **80**
   - Click "Add Ingress Rules"

6. Click **"Add Ingress Rules"** again

7. Add this rule:
   - Source CIDR: **0.0.0.0/0**
   - IP Protocol: **TCP**
   - Destination Port Range: **443**
   - Click "Add Ingress Rules"

---

## PART 4 — Connect to your VM via SSH

### On Windows laptop:

1. Open **PowerShell**

2. Move the key file to a safe location and fix permissions:
```powershell
# Replace with your actual paths
$keyFile = "C:\Users\ACER\Downloads\ssh-key-2026-05-01.key"
$ip = "YOUR_ORACLE_IP"   # paste your IP here

# Connect
ssh -i $keyFile ubuntu@$ip
```

3. If it asks "Are you sure you want to continue connecting?" → type **yes** → Enter

4. You should see a prompt like `ubuntu@optionsiq:~$`
   **You are now inside your Oracle VM.**

---

## PART 5 — Run the setup script (one command)

Inside your Oracle VM terminal, run:

```bash
curl -fsSL https://raw.githubusercontent.com/lookiniramya/trade-helper-app/main/oracle_setup.sh | bash
```

**If that fails** (file not yet on GitHub), do this instead:

```bash
git clone https://github.com/lookiniramya/trade-helper-app.git /opt/optionsiq
cd /opt/optionsiq
bash oracle_setup.sh
```

This takes **10-15 minutes**. You will see progress messages.
When it finishes you will see "Setup Complete!" with your IP address.

---

## PART 6 — Add your secrets

Still in the SSH terminal:

```bash
nano /opt/optionsiq/.env
```

You will see a text file. Edit these lines:

```
ANTHROPIC_API_KEY=sk-ant-api03-your-actual-key-here
OPTIONSIQ_APP_PASSWORD=your_password_here
```

To save:
- Press **Ctrl+X**
- Press **Y**
- Press **Enter**

Now restart the app to load the new secrets:

```bash
sudo supervisorctl restart optionsiq:*
```

---

## PART 7 — Test your app

1. On your phone or laptop browser, go to:
```
http://YOUR_ORACLE_IP
```

2. You should see the OptionsIQ password login page

3. Enter your password → OptionsIQ app loads

**If you see a connection error:** Wait 60 more seconds for the app to fully start, then refresh.

---

## PART 8 — Check everything is running

In the SSH terminal:

```bash
# See both services status
sudo supervisorctl status

# Should show:
# optionsiq-app        RUNNING   pid 12345, uptime 0:02:15
# optionsiq-collector  RUNNING   pid 12346, uptime 0:02:15
```

If any service shows STOPPED or ERROR:

```bash
# See what went wrong
tail -50 /var/log/optionsiq-app.log
tail -50 /var/log/optionsiq-collector.log
```

---

## PART 9 — Daily morning workflow (2 minutes from phone)

Paytm tokens expire at 6 AM IST every day. Each morning:

**Step 1:** Open the app on your phone → log in with your password

**Step 2:** In the sidebar, expand **"🔑 Refresh Token (Daily)"**

**Step 3:** Click **"Open Paytm Login →"** → complete Paytm login

**Step 4:** Paytm redirects you back. Copy the full URL from your browser address bar

**Step 5:** Paste it into the "Paste redirect URL" field → click **"Update Token & Restart"**

**Step 6:** App refreshes with new token. Select new expiry if it changed this week.

That's it. No SSH, no terminal, no laptop needed.

---

## PART 10 — Update the app when code changes

When you want to deploy new code from GitHub:

```bash
# SSH into VM
ssh -i key.pem ubuntu@YOUR_IP

# Pull latest code
cd /opt/optionsiq
sudo -u optionsiq git pull

# Restart
sudo supervisorctl restart optionsiq:*
```

Or set up auto-deploy (I can do this for you — it updates automatically on every GitHub push).

---

## Costs

| What | Cost |
|------|------|
| Oracle VM (2 OCPU, 12GB RAM) | **Free forever** |
| Storage 50GB | **Free forever** |
| Bandwidth 10TB/month | **Free forever** |
| Public IP | **Free forever** |
| Anthropic AI API calls | ~₹50-200/month |
| **Total** | **~₹50-200/month** |

---

## Troubleshooting

**App not loading on phone:**
```bash
sudo supervisorctl status
# If stopped: sudo supervisorctl start optionsiq:*
```

**"Connection refused" error:**
```bash
sudo ufw status
sudo systemctl status nginx
```

**Token not working:**
- Make sure you paste within 3 minutes of Paytm login
- Token link in the sidebar is faster than manually opening the URL

**App shows old data after restart:**
- The SQLite database persists at `/opt/optionsiq/optionsiq_data.db`
- Data survives restarts automatically

**Update weekly expiry every Thursday:**
- Use the "Refresh Token" panel in sidebar
- There's a "New expiry" field — update it when NIFTY weekly expiry changes

---

## What runs on Oracle 24/7

```
nginx          → serves app on port 80
streamlit      → your full dashboard
data_collector → collects option chain every 30s (9:15-3:30 only)
SQLite DB      → stores 2 days of price/OI/PCR history
```

When you open the app after hours or next morning, all yesterday's
data is loaded from the database automatically.
