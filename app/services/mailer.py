"""Envío de correos por el SMTP institucional (brief 1.3).

PENDIENTE (brief 4.2): disponibilidad y credenciales del SMTP. Sin
SMTP_HOST configurado, `is_configured()` es falso y los enlaces se comparten
de forma manual desde el panel.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.config import get_settings

log = logging.getLogger(__name__)


def is_configured() -> bool:
    s = get_settings()
    return bool(s.smtp_host and s.smtp_from)


def send(to: str, subject: str, body: str) -> bool:
    """Devuelve True si el servidor aceptó el mensaje. Nunca registra el contenido (contiene el enlace)."""
    s = get_settings()
    if not is_configured():
        return False
    msg = EmailMessage()
    msg["From"] = s.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(s.smtp_host or "", s.smtp_port, timeout=15) as smtp:
            if s.smtp_starttls:
                smtp.starttls(context=ssl.create_default_context())
            if s.smtp_user and s.smtp_password:
                smtp.login(s.smtp_user, s.smtp_password)
            smtp.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException):
        log.exception("No se pudo enviar el correo de invitación")
        return False


def invitation_email(nombre: str, enlace: str, horas: int) -> tuple[str, str]:
    subject = "RIIIACD: actualiza tu perfil en el directorio"
    body = (
        f"Hola, {nombre}:\n\n"
        "La coordinación de la Red de Investigación e Innovación en Inteligencia Artificial y Ciencia "
        "de Datos (RIIIACD) te invita a revisar y actualizar tu perfil en el directorio público.\n\n"
        f"Enlace personal (válido {horas} horas y de un solo uso):\n{enlace}\n\n"
        "Los cambios que propongas se publicarán después de que la coordinación los apruebe.\n"
        "Si no esperabas este mensaje, puedes ignorarlo.\n"
    )
    return subject, body
