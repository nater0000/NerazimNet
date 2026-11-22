import logging
from logging.handlers import RotatingFileHandler
import os
from utils.paths import get_app_data_dir

def setup_logger():
    """
    Configures the root logger for the entire application.
    It logs to a rotating file and to the console.
    """
    log_dir = os.path.join(get_app_data_dir(), 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, 'nerazimnet.log')

    logger = logging.getLogger()
    # --- CHANGE: Set to DEBUG to see all messages ---
    logger.setLevel(logging.DEBUG) 

    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Prevent adding duplicate handlers if this function is called multiple times
    if not logger.handlers:
        # Rotating file handler
        file_handler = RotatingFileHandler(
            log_file, maxBytes=1024*1024, backupCount=5
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        # Console stream handler
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

    # Third-party loggers are useless noise at DEBUG — PIL dumps a
    # "STREAM b'IHDR'" line per image chunk, urllib3 logs every request.
    for noisy in ('PIL', 'urllib3', 'customtkinter'):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.info("Logger initialized at DEBUG level.")
