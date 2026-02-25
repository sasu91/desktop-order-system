"""
Minimal structured logging configuration for desktop-order-system.

Provides:
- File logging for errors and warnings
- Console logging for critical errors only
- Automatic log rotation
"""
import logging
import logging.handlers
from pathlib import Path
from datetime import datetime


def setup_logging(
    log_dir: "str | Path | None" = None,
    app_name: str = "desktop_order_system",
) -> logging.Logger:
    """
    Setup structured logging with file output.

    Args:
        log_dir: Directory for log files (created if missing).  When *None*
                 the frozen-aware default location is used (next to .exe in
                 production, or <project_root>/logs in development).
        app_name: Application name for logger

    Returns:
        Configured logger instance
    """
    if log_dir is None:
        from .paths import get_logs_dir  # noqa: PLC0415
        log_path = get_logs_dir()
    else:
        log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    
    # Create logger
    logger = logging.getLogger(app_name)
    logger.setLevel(logging.DEBUG)
    
    # Avoid duplicate handlers
    if logger.handlers:
        return logger
    
    # File handler: rotating log (max 5MB, keep 3 backups)
    log_file = log_path / f"{app_name}_{datetime.now().strftime('%Y%m%d')}.log"
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,  # 5MB
        backupCount=3,
        encoding='utf-8',
    )
    file_handler.setLevel(logging.WARNING)  # File logs: warnings and errors only
    file_formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)
    
    # Console handler: critical errors only
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.CRITICAL)
    console_formatter = logging.Formatter('CRITICAL: %(message)s')
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)
    
    return logger


def get_logger(name: str = "desktop_order_system") -> logging.Logger:
    """
    Get configured logger instance.
    
    Args:
        name: Logger name (defaults to app logger)
    
    Returns:
        Logger instance
    """
    return logging.getLogger(name)
