#!/usr/bin/env python3
"""
Stinger - Scorpion Calibration Stand
Entry point for the application.
"""

import sys
import logging
import threading
from pathlib import Path

# Add app to path
sys.path.insert(0, str(Path(__file__).parent))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

from app.core.config import load_config, setup_logging
from app.core.version import __version__, __app_name__

logger = logging.getLogger(__name__)


def main():
    """Main entry point."""
    # Load configuration
    try:
        config = load_config()
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        print("Please ensure stinger_config.yaml exists in the project root.")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR loading config: {e}")
        sys.exit(1)
    
    # Setup logging
    setup_logging(config)
    
    logger.info(f"Starting {__app_name__} v{__version__}")

    # Defer heavy imports so startup reaches UI sooner.
    from app.ui import MainWindow
    from app.services.ui_bridge import UIBridge
    from app.services.work_order_controller import WorkOrderController
    from app.database.session import initialize_database

    # Initialize database in the background so failed/slow SQL handshakes
    # do not block first paint. Controller status polling will pick this up.
    def _init_database_async() -> None:
        db_config = config.get('database', {})
        if not initialize_database(db_config):
            logger.warning("Database connection failed - running in offline mode")
        else:
            logger.info("Database initialization completed")

    threading.Thread(target=_init_database_async, daemon=True).start()
    
    # Create Qt application
    app = QApplication(sys.argv)
    app.setApplicationName(__app_name__)
    app.setApplicationVersion(__version__)
    
    # Enable high DPI scaling for touch screens (if supported)
    if hasattr(Qt.ApplicationAttribute, "AA_UseHighDpiPixmaps"):
        app.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps)
    
    # Create UI bridge
    ui_bridge = UIBridge(config)
    
    # Create and show main window
    window = MainWindow(config, ui_bridge=ui_bridge)
    window.showMaximized()

    # Create work order controller (starts GUI-thread hardware polling timer)
    work_order_controller = WorkOrderController(ui_bridge, config)
    window.attach_work_order_controller(work_order_controller)
    
    # Run event loop
    exit_code = app.exec()
    
    # Cleanup
    logger.info("Shutting down...")
    work_order_controller.cleanup()
    
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
