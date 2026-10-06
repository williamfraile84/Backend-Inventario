import logging
import sys


def setup_logging():
    """Configura logging centralizado y estructurado para el backend de Inventario Fruver."""
    logging_format = "%(asctime)s [%(levelname)s] [%(name)s]: %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    logging.basicConfig(
        level=logging.INFO,
        format=logging_format,
        datefmt=date_format,
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )

    # Reducir ruido de librerías de bajo nivel
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("multipart").setLevel(logging.WARNING)
    logging.getLogger("pypdf").setLevel(logging.WARNING)
    logging.getLogger("PIL").setLevel(logging.WARNING)
    logging.getLogger("rapidocr_onnxruntime").setLevel(logging.WARNING)

    logger = logging.getLogger("inventario_fruver")
    logger.info("Logging centralizado estructurado inicializado exitosamente.")
    return logger


def get_logger(name: str = "inventario_fruver") -> logging.Logger:
    """Retorna un logger configurado para el módulo."""
    return logging.getLogger(name)

