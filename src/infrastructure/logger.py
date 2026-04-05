"""
Centralized logging configuration for the ERP application.
Provides consistent logging across all modules with both file and console output.
"""

import logging
import logging.handlers
import os
from pathlib import Path
from datetime import datetime


class LoggerConfig:
    """Centralized logger configuration"""
    
    # Log directory setup
    BASE_DIR = Path(__file__).parent.parent.parent
    LOG_DIR = BASE_DIR / "src" / "logs"
    LOG_DIR.mkdir(exist_ok=True)
    
    # Log file paths
    LOG_FILE = LOG_DIR / "erp_application.log"
    ERROR_LOG_FILE = LOG_DIR / "erp_errors.log"
    
    # Log format
    DETAILED_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
    SIMPLE_FORMAT = '%(asctime)s - %(levelname)s - %(message)s'
    TIMESTAMP_FORMAT = '%Y-%m-%d %H:%M:%S'


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """
    Get or create a configured logger instance.
    
    Args:
        name: Logger name (typically __name__)
        level: Logging level (default: INFO)
    
    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)
    
    # Avoid duplicate handlers
    if logger.handlers:
        return logger
    
    logger.setLevel(level)
    
    # Console handler (INFO and above for general output)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter(LoggerConfig.SIMPLE_FORMAT, LoggerConfig.TIMESTAMP_FORMAT)
    console_handler.setFormatter(console_formatter)
    
    # File handler (ALL logs)
    file_handler = logging.handlers.RotatingFileHandler(
        LoggerConfig.LOG_FILE,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(LoggerConfig.DETAILED_FORMAT, LoggerConfig.TIMESTAMP_FORMAT)
    file_handler.setFormatter(file_formatter)
    
    # Error file handler (ERROR and CRITICAL only)
    error_handler = logging.handlers.RotatingFileHandler(
        LoggerConfig.ERROR_LOG_FILE,
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
        encoding='utf-8'
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(file_formatter)
    
    # Add handlers
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    logger.addHandler(error_handler)
    
    return logger


# Custom exception classes for better error handling
class ERPException(Exception):
    """Base exception for all ERP-related errors"""
    pass


class DatabaseException(ERPException):
    """Exception for database-related errors"""
    pass


class DataValidationException(ERPException):
    """Exception for data validation errors"""
    pass


class ServiceException(ERPException):
    """Exception for service-related errors"""
    pass


class ImportException(ERPException):
    """Exception for import/export operation errors"""
    pass


class ConfigException(ERPException):
    """Exception for configuration errors"""
    pass
