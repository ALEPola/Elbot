# Performance Tuning Guide for ElBot Music

This guide helps you configure ElBot's music functionality for optimal performance on your system.

## Quick Start

The bot automatically adapts to your system, but you can optimize performance by setting the `MUSIC_STRATEGY` environment variable based on your hardware:

### For Raspberry Pi / Low-Power Systems
```bash
# Use Lavalink first (faster, less CPU intensive)
export MUSIC_STRATEGY=lavalink_first

# If Lavalink is unreliable, try parallel resolution
export MUSIC_STRATEGY=parallel
```

### For High-Performance PCs
```bash
# Use parallel resolution for fastest response
export MUSIC_STRATEGY=parallel

# Or use the default Lavalink-first approach
export MUSIC_STRATEGY=lavalink_first
```

### For Systems with Limited Internet
```bash
# Try fallback first if YouTube blocks Lavalink frequently
export MUSIC_STRATEGY=fallback_first
```

## Resolution Strategies Explained

### 1. **lavalink_first** (Default - Recommended for most systems)
- Tries Lavalink node first (fast)
- Falls back to yt-dlp if Lavalink fails
- Best for: Most systems, especially low-power devices
- Response time: 0.5-2 seconds

### 2. **fallback_first** (Legacy behavior)
- Tries yt-dlp extraction first (slow but reliable)
- Falls back to Lavalink if yt-dlp fails
- Best for: Systems where Lavalink frequently fails
- Response time: 2-5 seconds

### 3. **parallel** (Best for high-performance systems)
- Tries both methods simultaneously
- Uses whichever succeeds first
- Best for: Powerful systems with good internet
- Response time: 0.5-2 seconds (uses fastest available)

## Setting the Strategy

### Method 1: Environment Variable (Temporary)
```bash
# Linux/Mac/Pi
export MUSIC_STRATEGY=lavalink_first
python -m elbot

# Windows Command Prompt
set MUSIC_STRATEGY=lavalink_first
python -m elbot

# Windows PowerShell
$env:MUSIC_STRATEGY="lavalink_first"
python -m elbot
```

### Method 2: .env File (Permanent)
Add to your `.env` file:
```env
MUSIC_STRATEGY=lavalink_first
```

### Method 3: Systemd Service (For Linux/Pi)
Edit your service file:
```bash
sudo systemctl edit elbot
```

Add:
```ini
[Service]
Environment="MUSIC_STRATEGY=lavalink_first"
```

## Performance Optimization Tips

### For Raspberry Pi / ARM Devices

1. **Use Lavalink-first strategy:**
   ```bash
   export MUSIC_STRATEGY=lavalink_first
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

1. **Use parallel strategy for fastest response:**
   ```bash
   export MUSIC_STRATEGY=parallel
   ```

2. **Increase Lavalink memory** in startup script:
   ```bash
   java -Xmx2G -jar Lavalink.jar
   ```

### For Windows PCs

1. **Use parallel or default strategy:**
   ```powershell
   $env:MUSIC_STRATEGY="parallel"
   ```

2. **Ensure Windows Defender exclusions** for bot directory

### For Docker Deployments

Add to `docker-compose.yml`:
```yaml
services:
  elbot:
    environment:
      - MUSIC_STRATEGY=lavalink_first
```

## Troubleshooting Slow Response

### Symptom: "The application did not respond"

1. **Check strategy is set correctly:**
   ```bash
   echo $MUSIC_STRATEGY
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
| Raspberry Pi 3/4 | ARM Cortex | 1-4GB | lavalink_first | 1-3 seconds |
| Cloud VPS | 2+ vCPU | 2GB+ | parallel | 0.5-1.5 seconds |
| Gaming PC | 4+ cores | 8GB+ | parallel | 0.3-1 second |
| Old laptop | 2 cores | 4GB | lavalink_first | 1-2 seconds |
| Docker (limited) | 1 vCPU | 512MB | lavalink_first | 2-3 seconds |

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
   - Current MUSIC_STRATEGY setting
   - Output from `/ytcheck`
   - Relevant log entries
