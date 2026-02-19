# Performance Tuning Guide for ElBot Music

This guide helps you configure ElBot's music functionality for optimal performance on your system.

## Quick Start

The bot automatically adapts to your system, but you can optimize performance by setting the `ELBOT_PRIMARY_BACKEND` environment variable based on your hardware:

### For Raspberry Pi / Low-Power Systems
```bash
# Use Lavalink first (faster, less CPU intensive)
export ELBOT_PRIMARY_BACKEND=lavalink

# If Lavalink is unreliable, try fallback resolution
export ELBOT_PRIMARY_BACKEND=fallback
```

### For High-Performance PCs
```bash
# Keep fallback as the primary backend (default behavior)
export ELBOT_PRIMARY_BACKEND=fallback

# Or prefer Lavalink first if your node is stable and low latency
export ELBOT_PRIMARY_BACKEND=lavalink
```

### For Systems with Limited Internet
```bash
# Try fallback first if YouTube blocks Lavalink frequently
export ELBOT_PRIMARY_BACKEND=fallback
```

## Resolution Strategies Explained

### 1. **fallback** (Default)
- Starts yt-dlp immediately
- Starts Lavalink after a short hedge delay
- Best for: Reliability when Lavalink behavior is inconsistent
- Response time: 1-5 seconds (depends on source)

### 2. **lavalink**
- Tries Lavalink node first (fast)
- Falls back to yt-dlp if Lavalink fails
- Best for: Systems with a healthy, low-latency Lavalink node
- Response time: 0.5-2 seconds

### Optional hedge timing knobs
- `ELBOT_FALLBACK_HEDGE_DELAY` (default `1.5`) controls when yt-dlp starts when `ELBOT_PRIMARY_BACKEND=lavalink`
- `ELBOT_LAVALINK_HEDGE_DELAY` (default `0.0`) controls when Lavalink starts when `ELBOT_PRIMARY_BACKEND=fallback`

## Setting the Strategy

### Method 1: Environment Variable (Temporary)
```bash
# Linux/Mac/Pi
export ELBOT_PRIMARY_BACKEND=lavalink
python -m elbot

# Windows Command Prompt
set ELBOT_PRIMARY_BACKEND=lavalink
python -m elbot

# Windows PowerShell
$env:ELBOT_PRIMARY_BACKEND="lavalink"
python -m elbot
```

### Method 2: .env File (Permanent)
Add to your `.env` file:
```env
ELBOT_PRIMARY_BACKEND=lavalink
```

### Method 3: Systemd Service (For Linux/Pi)
Edit your service file:
```bash
sudo systemctl edit elbot
```

Add:
```ini
[Service]
Environment="ELBOT_PRIMARY_BACKEND=lavalink"
```

## Performance Optimization Tips

### For Raspberry Pi / ARM Devices

1. **Use Lavalink-first strategy:**
   ```bash
   export ELBOT_PRIMARY_BACKEND=lavalink
   ```

2. **Reduce Lavalink buffer sizes** in `application.yml`:
   ```yaml
   lavalink:
     server:
       bufferDurationMs: 100      # Reduce from 400
       frameBufferDurationMs: 1000 # Reduce from 5000
   ```

3. **Increase swap space:**
   ```bash
   sudo dphys-swapfile swapoff
   sudo nano /etc/dphys-swapfile
   # Set CONF_SWAPSIZE=2048
   sudo dphys-swapfile setup
   sudo dphys-swapfile swapon
   ```

4. **Use a faster SD card** (Class 10 or better)

### For Cloud VPS / Servers

1. **Start with fallback strategy for reliability:**
   ```bash
   export ELBOT_PRIMARY_BACKEND=fallback
   ```

2. **Increase Lavalink memory** in startup script:
   ```bash
   java -Xmx2G -jar Lavalink.jar
   ```

### For Windows PCs

1. **Use fallback (default) or lavalink strategy:**
   ```powershell
   $env:ELBOT_PRIMARY_BACKEND="fallback"
   ```

2. **Ensure Windows Defender exclusions** for bot directory

### For Docker Deployments

Add to `docker-compose.yml`:
```yaml
services:
  elbot:
    environment:
      - ELBOT_PRIMARY_BACKEND=lavalink
```

## Troubleshooting Slow Response

### Symptom: "The application did not respond"

1. **Check strategy is set correctly:**
   ```bash
   echo $ELBOT_PRIMARY_BACKEND
   ```

2. **Verify Lavalink is running:**
   ```bash
   curl -H "Authorization: youshallnotpass" http://localhost:2333/version
   ```

3. **Check system resources:**
   ```bash
   # CPU and Memory
   htop
   
   # Disk I/O
   iotop
   
   # Network
   ping youtube.com
   ```

4. **Test component speeds:**
   ```bash
   # Test Lavalink
   time curl http://localhost:2333/version
   
   # Test yt-dlp
   time yt-dlp --dump-json "ytsearch:test"
   ```

### Symptom: Frequent fallbacks to yt-dlp

1. **Check Lavalink logs:**
   ```bash
   tail -f logs/spring.log
   ```

2. **Update YouTube plugin:**
   - Download latest from: https://github.com/lavalink-devs/youtube-source/releases

3. **Set YouTube cookies:**
   ```bash
   export YT_COOKIES_FILE=/path/to/cookies.txt
   ```

## Monitoring Performance

The bot includes built-in diagnostics. Use the `/ytcheck` command to see:
- Lavalink latency
- Resolution strategy effectiveness
- Fallback usage statistics
- Average startup times

## Recommended Configurations by System

| System Type | CPU | RAM | Strategy | Expected Response Time |
|------------|-----|-----|----------|------------------------|
| Raspberry Pi 3/4 | ARM Cortex | 1-4GB | lavalink | 1-3 seconds |
| Cloud VPS | 2+ vCPU | 2GB+ | fallback | 0.5-1.5 seconds |
| Gaming PC | 4+ cores | 8GB+ | fallback | 0.3-1 second |
| Old laptop | 2 cores | 4GB | lavalink | 1-2 seconds |
| Docker (limited) | 1 vCPU | 512MB | lavalink | 2-3 seconds |

## Advanced Tuning

### Custom Timeout Values

For very slow systems, you can modify timeouts in the code:
- Backend connection timeout: `wait_ready(timeout=10.0)`
- Lavalink retry delays: Starts at 0.5s, doubles each retry
- yt-dlp extraction: No timeout (runs in thread)

### Network Optimization

1. **Use wired connection** instead of WiFi when possible
2. **Configure DNS** for faster resolution:
   ```bash
   # Use Cloudflare DNS
   echo "nameserver 1.1.1.1" | sudo tee /etc/resolv.conf
   ```

3. **Enable TCP BBR** (Linux only):
   ```bash
   echo "net.core.default_qdisc=fq" | sudo tee -a /etc/sysctl.conf
   echo "net.ipv4.tcp_congestion_control=bbr" | sudo tee -a /etc/sysctl.conf
   sudo sysctl -p
   ```

## Getting Help

If you're still experiencing issues:

1. Run diagnostics: `/ytcheck`
2. Check logs: `sudo journalctl -u elbot -f`
3. Try different strategies
4. Report issues with:
   - System specs (CPU, RAM, OS)
   - Current ELBOT_PRIMARY_BACKEND setting
   - Output from `/ytcheck`
   - Relevant log entries
