import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import config

logger = logging.getLogger(__name__)


def send_report(subject: str, html_body: str) -> bool:
    """Send an HTML email via SMTP to configured recipients.

    Returns True if sent successfully, False otherwise.
    """
    if not config.SMTP_USER or not config.SMTP_PASSWORD:
        logger.warning("SMTP credentials not configured, skipping email send")
        return False

    if not config.RECIPIENTS:
        logger.warning("No recipients configured, skipping email send")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = config.SMTP_FROM
    msg["To"] = ", ".join(config.RECIPIENTS)

    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.sendmail(config.SMTP_FROM, config.RECIPIENTS, msg.as_string())

        logger.info("Email sent to %s", ", ".join(config.RECIPIENTS))
        return True

    except smtplib.SMTPException as e:
        logger.error("Failed to send email: %s", e)
        return False
