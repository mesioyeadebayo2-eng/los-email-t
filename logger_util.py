import os
import logging
from logging.handlers import RotatingFileHandler
import config

_configured = False

def setup_logging():
    global _configured
    if _configured:
        return
    
    os.makedirs(config.DATA_DIR, exist_ok=True)
    
    log_format = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(fmt=log_format, datefmt=date_format)
    
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    # Avoid duplicate handlers if reloaded
    if not root_logger.handlers:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        console_handler.setLevel(logging.INFO)
        root_logger.addHandler(console_handler)
    else:
        for handler in root_logger.handlers:
            handler.setFormatter(formatter)
            
    # Add RotatingFileHandler (5MB x 3)
    file_handler_exists = any(isinstance(h, RotatingFileHandler) for h in root_logger.handlers)
    if not file_handler_exists:
        file_handler = RotatingFileHandler(
            filename=config.LOG_PATH,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.INFO)
        root_logger.addHandler(file_handler)
        
    _configured = True

# Automatically setup logging when logger_util is imported
setup_logging()
