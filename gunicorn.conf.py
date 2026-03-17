"""
Gunicorn configuration for production deployment on Digital Ocean.
Usage: gunicorn -c gunicorn.conf.py "app:app"
"""
import multiprocessing

# Bind to all interfaces; nginx handles SSL and proxying
bind = "0.0.0.0:5000"

# 2 workers per CPU core is a good starting point for I/O-bound apps
workers = multiprocessing.cpu_count() * 2 + 1

# Restart workers after this many requests to avoid memory leaks
max_requests = 1000
max_requests_jitter = 100

# Timeouts
timeout = 30
graceful_timeout = 10
keepalive = 2

# Logging
loglevel = "info"
accesslog = "-"   # stdout
errorlog  = "-"   # stderr

# Process name
proc_name = "precinct-finder"
