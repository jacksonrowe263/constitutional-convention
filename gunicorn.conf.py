import os

bind = f"0.0.0.0:{os.environ.get('PORT', '5000')}"
timeout = 300  # 5 minutes — AI document generation can be slow
workers = 1
accesslog = "-"
errorlog = "-"
loglevel = "info"
