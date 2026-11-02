#!/usr/bin/env python3
"""Submits a TAB to the HILShare service, follows the job and reports the run.

README.md has the exit codes.
"""

import base64
import json
import os
import re
import secrets
import signal
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ElementTree
from http.client import HTTPException
from pathlib import Path
from time import monotonic, sleep

SERVICE_URL = "https://hilshare.anmolkathail.workers.dev"
OIDC_AUDIENCE = "hilshare"
# Cloudflare can refuse the default User-Agent of urllib.
USER_AGENT = "hilshare-action/1"
README_URL = "https://github.com/hilshare/action#exit-codes"

# From the start of the step, in the queue and on owner_busy.
WAIT_LIMIT_SECONDS = 3600
# An outage of a host and a full queue often end soon, so a refusal waits this long.
BOARD_WAIT_LIMIT_SECONDS = 600
SUBMIT_AGAIN_WAIT_SECONDS = 30
SUBMIT_ATTEMPTS = 3
SUBMIT_RETRY_WAIT_SECONDS = 30
SUBMIT_TIMEOUT_SECONDS = 60
# The service expires a queued job after 180 s with no status request.
POLL_ATTEMPTS = 6
POLL_RETRY_WAIT_SECONDS = 15
POLL_TIMEOUT_SECONDS = 30
# The service ends a running job within 660 s: 600 s and one cron minute.
RUNNING_LIMIT_SECONDS = 720
# The runner sends SIGTERM 7.5 s after SIGINT.
CANCEL_TIMEOUT_SECONDS = 5

HIGHEST_HARNESS_CODE = 11
QUEUE_FULL = 20
BOARD_NOT_AVAILABLE = 21
REFUSED = 22
HOST_LOST = 23
WAIT_LIMIT = 24
JOB_ENDED_WITH_NO_RESULT = 25
SERVICE_NOT_AVAILABLE = 26
WORKFLOW_INCORRECT = 27
UNEXPECTED_PASS = 28
DAILY_LIMIT = 29

JOB_KEYS = ("job_id", "job_token", "poll_after_seconds")
BOARD_ERRORS = {"board_offline", "board_missing", "board_paused"}
WAIT_ERRORS = {"board_offline", "board_missing", "queue_full"}
TRANSIENT_ERRORS = {"key_set_unavailable", "account_lookup_failed", "internal_error"}

CONTROL_CHARACTERS = re.compile("[\x00-\x08\x0b-\x1f\x7f]")


class Ended(Exception):
    """The action ends with an exit code of its own and a sentence."""

    def __init__(self, code, sentence):
        super().__init__(sentence)
        self.code = code
        self.sentence = sentence


class Unavailable(Exception):
    """No answer in the form of the API: a network failure, or a body that is not JSON."""


class Cancelled(Exception):
    """The runner sent a signal, because the workflow was cancelled."""


def raise_cancelled(_signal_number, _frame):
    raise Cancelled


def say(text):
    print(f"hilshare: {text}")


def escape_data(text):
    """The escapes of a workflow command value."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def annotate(text):
    print(f"::error::{escape_data(text)}")


def write_output(name, value):
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"{name}={value}\n")


def write_summary(text):
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
        summary.write(f"{text}\n")


def send(method, url, credential, body=None, timeout_seconds=POLL_TIMEOUT_SECONDS):
    """One request: the HTTP status and the JSON object of the answer."""
    headers = {"Authorization": f"Bearer {credential}", "User-Agent": USER_AGENT}
    if body is not None:
        headers["Content-Type"] = "application/octet-stream"
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                status, text = response.status, response.read()
        except urllib.error.HTTPError as error:
            status, text = error.code, error.read()
    except (OSError, HTTPException) as error:
        raise Unavailable(str(error)) from error
    try:
        answer = json.loads(text)
    except ValueError as error:
        raise Unavailable(f"HTTP {status} with a body that is not JSON") from error
    if not isinstance(answer, dict):
        raise Unavailable(f"HTTP {status} with a body that is not an object")
    return status, answer


def oidc_token():
    """A new token: the service accepts each token one time, and a refusal also uses it."""
    url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL")
    request_token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not url or not request_token:
        raise Ended(
            WORKFLOW_INCORRECT,
            "the job has no OIDC token; add `permissions: id-token: write` to the job, "
            "and skip this step for a pull request from a fork",
        )
    status, answer = send("GET", f"{url}&audience={OIDC_AUDIENCE}", request_token)
    if status != 200 or not answer.get("value"):
        raise Unavailable(f"GitHub gave no OIDC token: HTTP {status}")
    return answer["value"]


def submit(tab, board, deadline):
    """The job that the service accepted. Waits on owner_busy until `deadline`."""
    url = f"{SERVICE_URL}/v1/jobs?board={urllib.parse.quote(board, safe='')}"
    board_deadline = monotonic() + BOARD_WAIT_LIMIT_SECONDS
    attempts = 0
    while True:
        attempts += 1
        try:
            status, answer = send("POST", url, oidc_token(), tab, SUBMIT_TIMEOUT_SECONDS)
            error = answer.get("error")
            if status == 201 and all(answer.get(key) for key in JOB_KEYS):
                return answer
            if error == "owner_busy":
                if monotonic() + SUBMIT_AGAIN_WAIT_SECONDS >= deadline:
                    raise Ended(
                        WAIT_LIMIT,
                        f"the owner still has another job after {WAIT_LIMIT_SECONDS // 60} minutes",
                    )
                say("the owner has another job; this step waits and submits again")
                sleep(SUBMIT_AGAIN_WAIT_SECONDS)
                attempts = 0
                continue
            message = answer.get("message", "the answer has no sentence")
            if error in WAIT_ERRORS and monotonic() + SUBMIT_AGAIN_WAIT_SECONDS < board_deadline:
                say(f"{message}; this step waits and submits again")
                sleep(SUBMIT_AGAIN_WAIT_SECONDS)
                attempts = 0
                continue
            if error == "queue_full":
                raise Ended(QUEUE_FULL, message)
            if error in BOARD_ERRORS:
                raise Ended(BOARD_NOT_AVAILABLE, message)
            if error == "daily_limit":
                raise Ended(DAILY_LIMIT, message)
            if error is None or error in TRANSIENT_ERRORS or status >= 500:
                raise Unavailable(f"HTTP {status}: {message}")
            raise Ended(REFUSED, f"the service refused the job ({error}): {message}")
        except Unavailable as failure:
            if attempts >= SUBMIT_ATTEMPTS:
                raise Ended(
                    SERVICE_NOT_AVAILABLE,
                    f"the service gave no answer after {attempts} attempts: {failure}",
                ) from failure
            say(f"attempt {attempts} failed: {failure}")
            sleep(SUBMIT_RETRY_WAIT_SECONDS)


def job_url(job):
    return f"{SERVICE_URL}/v1/jobs/{job['job_id']}"


def cancel(job):
    """Cancels a queued job. False when the job is on the board. With no answer, the service
    expires the job after 180 s."""
    try:
        status, answer = send(
            "DELETE", job_url(job), job["job_token"], timeout_seconds=CANCEL_TIMEOUT_SECONDS
        )
    except Unavailable as failure:
        say(f"the cancel got no answer: {failure}")
        return True
    return not (status == 409 and answer.get("error") == "job_running")


def follow(job, deadline):
    """The answer of the service for the job in a final state."""
    short_id = job["job_id"][:8]
    wait_seconds = job["poll_after_seconds"]
    progress = None
    failures = 0
    while True:
        sleep(wait_seconds)
        try:
            status, answer = send("GET", job_url(job), job["job_token"])
            if status == 404:
                raise Ended(JOB_ENDED_WITH_NO_RESULT, f"the service no longer has job {short_id}")
            if status != 200:
                raise Unavailable(f"HTTP {status}: {answer.get('message')}")
        except Unavailable as failure:
            failures += 1
            if failures >= POLL_ATTEMPTS:
                raise Ended(
                    SERVICE_NOT_AVAILABLE,
                    f"no status for job {short_id} after {failures} attempts: {failure}",
                ) from failure
            say(f"status attempt {failures} failed: {failure}")
            wait_seconds = POLL_RETRY_WAIT_SECONDS
            continue
        failures = 0
        state = answer.get("state")
        current = f"position {answer.get('position')}" if state == "queued" else state
        if current != progress:
            say(f"job {short_id}: {current}")
            progress = current
        if monotonic() >= deadline + RUNNING_LIMIT_SECONDS:
            raise Ended(HOST_LOST, f"job {short_id} has no result after the wait limit")
        if state == "queued" and monotonic() >= deadline and cancel(job):
            raise Ended(
                WAIT_LIMIT,
                f"job {short_id} did not reach the board in {WAIT_LIMIT_SECONDS // 60} minutes; "
                "this step removed it from the queue",
            )
        if answer.get("poll_after_seconds") is None:
            return answer
        wait_seconds = answer["poll_after_seconds"]


def printable(log):
    """The text of a log for the job log: kernel sentinels as ␞, no other control byte."""
    text = log.decode("utf-8", "replace").replace("\r\n", "\n").replace("\x1e", "␞")
    return CONTROL_CHARACTERS.sub("�", text)


def print_log(log):
    """An app wrote this text, so a stop-commands guard keeps it from the runner."""
    guard = secrets.token_hex(16)
    print(f"::stop-commands::{guard}")
    print(printable(log))
    print(f"::{guard}::")


def junit_errors(junit):
    """One sentence for each failed check or run error that the harness wrote."""
    try:
        root = ElementTree.fromstring(junit)
    except ElementTree.ParseError:
        return []
    sentences = []
    for testcase in root.iter("testcase"):
        for failure in testcase.iter("failure"):
            sentences.append(f"{testcase.get('name')}: {failure.get('message')}")
        for error in testcase.iter("error"):
            sentences.append(error.get("message", ""))
    return sentences


def report(job, answer, app, board, expected_exit):
    """Prints and stores the result of a finished job. The exit code of the step."""
    result = answer["result"]
    exit_code = result["exit_code"]
    # With the board in the name, one TAB on two boards keeps two artifacts.
    stem = re.sub(r"[^A-Za-z0-9._-]", "-", f"{Path(app).stem}-{board}")
    directory = Path(os.environ["RUNNER_TEMP"]) / "hilshare" / stem
    directory.mkdir(parents=True, exist_ok=True)
    log = base64.b64decode(result["log_base64"])
    (directory / "hilshare.log").write_bytes(log)
    (directory / "hilshare-results.xml").write_text(result["junit"], encoding="utf-8")
    write_output("directory", directory)
    write_output("artifact-name", f"hilshare-{stem}")
    write_output("exit-code", exit_code)

    print_log(log)
    write_summary(result["summary"])
    write_summary(f"Job `{job['job_id'][:8]}`. [Service status]({SERVICE_URL}).")
    if exit_code == expected_exit:
        say(f"the run ended at exit code {exit_code}, as expected")
        if expected_exit != 0:
            write_summary(
                f"\nThis run expected exit {expected_exit} and got it. "
                "The red above is the fixture, not a regression."
            )
        return 0
    for sentence in junit_errors(result["junit"]):
        annotate(sentence)
    annotate(f"the run ended at exit code {exit_code} and expected {expected_exit}; {README_URL}")
    return UNEXPECTED_PASS if exit_code == 0 else exit_code


def run(app, board, expected_exit_text):
    """The whole action. The exit code of the step."""
    deadline = monotonic() + WAIT_LIMIT_SECONDS
    if not board:
        raise Ended(WORKFLOW_INCORRECT, "the input `board` is empty")
    if not re.fullmatch("[0-9]+", expected_exit_text) or (
        int(expected_exit_text) > HIGHEST_HARNESS_CODE
    ):
        raise Ended(WORKFLOW_INCORRECT, f"expected-exit is {expected_exit_text!r}, not 0 to 11")
    expected_exit = int(expected_exit_text)
    try:
        tab = Path(app).read_bytes()
    except OSError as error:
        raise Ended(WORKFLOW_INCORRECT, f"the TAB cannot be read: {error}") from error

    job = submit(tab, board, deadline)
    write_output("job-id", job["job_id"])
    say(f"job {job['job_id'][:8]}: submitted to {board}")
    try:
        answer = follow(job, deadline)
    except Cancelled:
        cancel(job)
        raise Ended(JOB_ENDED_WITH_NO_RESULT, "the workflow was cancelled") from None
    state = answer.get("state")
    if state == "host_lost":
        raise Ended(HOST_LOST, f"the host of {board} lost job {job['job_id'][:8]}; run the job again")
    if state != "finished":
        raise Ended(JOB_ENDED_WITH_NO_RESULT, f"job {job['job_id'][:8]} is {state}, with no result")
    try:
        return report(job, answer, app, board, expected_exit)
    except (KeyError, TypeError, ValueError) as error:
        raise Ended(SERVICE_NOT_AVAILABLE, f"the result is not valid: {error!r}") from error


def act():
    """Runs the action from its environment. The exit code of the step."""
    board = os.environ["BOARD"]
    try:
        return run(os.environ["APP"], board, os.environ["EXPECTED_EXIT"])
    except Cancelled:
        ended = Ended(JOB_ENDED_WITH_NO_RESULT, "the workflow was cancelled")
    except Ended as error:
        ended = error
    annotate(ended.sentence)
    write_output("exit-code", ended.code)
    write_summary(
        f"### HILShare — {board} — NOT RUN\n\n{ended.sentence}\n\n"
        f"Exit code {ended.code}. [The exit codes]({README_URL})."
    )
    return ended.code


def main():
    signal.signal(signal.SIGINT, raise_cancelled)
    signal.signal(signal.SIGTERM, raise_cancelled)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    return act()


if __name__ == "__main__":
    sys.exit(main())
