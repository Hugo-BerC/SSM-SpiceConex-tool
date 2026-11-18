"""Themed dialogs and sanitized operator diagnostics."""
from pathlib import Path
from PySide6.QtWidgets import QMessageBox, QFileDialog, QDialog
from aws.errors import classify_error
from utils.redaction import redact_sensitive

def _exception_summary(context: str, exc: Exception) -> tuple[str, str]:
    message = redact_sensitive(str(exc).strip() or exc.__class__.__name__)
    text = message.lower()
    category = classify_error(exc)

    if "profile" in text and ("not found" in text or "could not be found" in text):
        summary = f"The AWS profile required for {context} could not be found."
        action = "Check the selected profile and run 'aws configure list-profiles' if necessary."
    elif "unable to locate credentials" in text or "no credentials" in text:
        summary = f"No AWS credentials are available to perform {context}."
        action = "Authenticate with AWS SSO or configure the selected AWS credential source, then try again."
    elif category == "authentication":
        summary = f"The AWS session required for {context} is unavailable or expired."
        action = "Refresh the selected AWS credential source or SSO session and try again."
    elif category == "permission":
        summary = f"AWS denied the permissions required for {context}."
        action = "Check the IAM policy/role used by the selected profile and confirm it allows the requested AWS API operation."
    elif category == "throttling":
        summary = f"AWS throttled the requests required for {context}."
        action = "The bounded SDK retries were exhausted. Wait before retrying or reduce the query scope."
    elif category == "network":
        summary = f"AWS could not be reached while attempting {context}."
        action = "Check network connectivity, proxy settings and the selected AWS region."
    elif "validation" in context.lower():
        summary = "The AWS session could not be validated."
        action = "Check the selected profile, region and active AWS/SSO session, then try again."
    else:
        summary = f"{context} could not be completed."
        action = "Review the details below and verify the selected AWS context before retrying."

    return summary, f"{action}\n\nTechnical details:\n{message}"


def show_dialog(parent, level: QMessageBox.Icon, title: str, summary: str,
                details: str | None = None) -> None:
    box = QMessageBox(level, title, redact_sensitive(summary), QMessageBox.StandardButton.Ok, parent)
    if details:
        box.setDetailedText(redact_sensitive(details))
    box.exec()


def show_exception(parent, title: str, context: str, exc: Exception,
                   level: QMessageBox.Icon = QMessageBox.Icon.Critical) -> None:
    summary, details = _exception_summary(context, exc)
    show_dialog(parent, level, title, summary, details)


def confirm_operation(parent, title: str, summary: str, details: str = "") -> bool:
    """Require a deliberate operator decision before an AWS write operation."""
    box = QMessageBox(QMessageBox.Icon.Warning, title, summary, parent=parent)
    box.setInformativeText(details)
    box.setStandardButtons(QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes)
    box.setDefaultButton(QMessageBox.StandardButton.Cancel)
    return box.exec() == QMessageBox.StandardButton.Yes


def _open_csv_dialog(parent, title: str) -> str:
    """Open the Qt widget-based CSV picker so it follows the SpiceConex theme."""
    dialog = QFileDialog(parent, title, str(Path.home()), "CSV files (*.csv);;All files (*)")
    # The native picker is outside our stylesheet. Force the Qt widget dialog
    # so the file browser uses the same Arrakis/SpiceConex visual system.
    dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
    dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
    dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
    dialog.resize(900, 600)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        files = dialog.selectedFiles()
        return files[0] if files else ""
    return ""
