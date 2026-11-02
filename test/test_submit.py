"""submit.py against a fake service in this process. No network and no board."""

import base64
import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import submit  # noqa: E402

JOB_ID = "0b0d9c8e-2f4a-4c1e-9a57-3f1f2d6c7b10"
JOB_TOKEN = "a" * 64
ACCEPTED = (201, {"job_id": JOB_ID, "job_token": JOB_TOKEN, "state": "queued", "position": 1, "poll_after_seconds": 10})
QUEUED = (200, {"state": "queued", "position": 2, "poll_after_seconds": 20})
RUNNING = (200, {"state": "running", "position": None, "poll_after_seconds": 10})
PASS_JUNIT = '<?xml version="1.0"?><testsuites><testsuite><testcase name="crc32"/></testsuite></testsuites>'
FAIL_JUNIT = (
    '<?xml version="1.0"?><testsuites><testsuite><testcase name="crc32">'
    '<failure message="the app reported status=FAIL"/></testcase></testsuite></testsuites>'
)
PREFLIGHT_JUNIT = (
    '<?xml version="1.0"?><testsuites><testsuite><testcase name="hilshare-run">'
    '<error message="the TBF needs Tock 2.4; build it for 2.3" type="ConfigError"/>'
    "</testcase></testsuite></testsuites>"
)


def finished(exit_code, junit=PASS_JUNIT, log=b"text\r\n"):
    result = {
        "exit_code": exit_code,
        "installs": 1,
        "log_base64": base64.b64encode(log).decode(),
        "junit": junit,
        "summary": f"### HILShare — exit {exit_code}",
    }
    return (200, {"state": "finished", "position": None, "poll_after_seconds": None, "result": result})


def ended(state):
    return (200, {"state": state, "position": None, "poll_after_seconds": None, "result": None})


def refusal(status, error, message="a sentence"):
    return (status, {"error": error, "message": message})


class Clock:
    """monotonic() and sleep() with no real time."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class FakeService(ThreadingHTTPServer):
    """Gives each request the next answer of `answers`, and records each request."""

    def __init__(self, answers):
        super().__init__(("127.0.0.1", 0), Handler)
        self.answers = list(answers)
        self.requests = []
        self.tokens_issued = 0

    def url(self):
        return f"http://127.0.0.1:{self.server_address[1]}"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_arguments):
        pass

    def answer(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        if self.path.startswith("/token"):
            self.server.tokens_issued += 1
            status, body = 200, json.dumps({"value": f"token-{self.server.tokens_issued}"}).encode()
        else:
            self.server.requests.append((self.command, self.path, self.headers.get("Authorization")))
            if self.command == "DELETE":
                status, answer = self.server.delete_answer
            else:
                status, answer = self.server.answers.pop(0)
            body = answer if isinstance(answer, bytes) else json.dumps(answer).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_DELETE = answer


class SubmitTests(unittest.TestCase):
    def start(self, answers, delete_answer=(200, {"state": "cancelled"})):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.app = self.directory / "my-app.tab"
        self.app.write_bytes(b"tab")
        self.service = FakeService(answers)
        self.service.delete_answer = delete_answer
        thread = threading.Thread(
            target=self.service.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        thread.start()
        self.addCleanup(self.service.server_close)
        self.addCleanup(self.service.shutdown)
        self.clock = Clock()
        environment = {
            "ACTIONS_ID_TOKEN_REQUEST_URL": f"{self.service.url()}/token?api-version=2.0",
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "request-token",
            "GITHUB_OUTPUT": str(self.directory / "output"),
            "GITHUB_STEP_SUMMARY": str(self.directory / "summary"),
            "RUNNER_TEMP": str(self.directory),
            "APP": str(self.app),
            "BOARD": "nucleo-f411re",
            "EXPECTED_EXIT": "0",
        }
        for patcher in (
            mock.patch.dict(os.environ, environment),
            mock.patch.object(submit, "SERVICE_URL", self.service.url()),
            mock.patch.object(submit, "monotonic", self.clock.monotonic),
            mock.patch.object(submit, "sleep", self.clock.sleep),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_main(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = submit.act()
        self.stdout = stdout.getvalue()
        return code

    def outputs(self):
        lines = (self.directory / "output").read_text().splitlines()
        return dict(line.split("=", 1) for line in lines)

    def summary(self):
        return (self.directory / "summary").read_text()

    def test_a_pass_exits_zero_and_stores_the_files(self):
        self.start([ACCEPTED, finished(0)])
        self.assertEqual(self.run_main(), 0)
        outputs = self.outputs()
        self.assertEqual(outputs["exit-code"], "0")
        self.assertEqual(outputs["job-id"], JOB_ID)
        self.assertEqual(outputs["artifact-name"], "hilshare-my-app-nucleo-f411re")
        directory = Path(outputs["directory"])
        self.assertEqual((directory / "hilshare.log").read_bytes(), b"text\r\n")
        self.assertEqual((directory / "hilshare-results.xml").read_text(), PASS_JUNIT)
        self.assertIn("### HILShare — exit 0", self.summary())
        self.assertNotIn("::error::", self.stdout)

    def test_each_request_carries_its_credential(self):
        self.start([ACCEPTED, finished(0)])
        self.run_main()
        self.assertEqual(
            self.service.requests,
            [
                ("POST", "/v1/jobs?board=nucleo-f411re", "Bearer token-1"),
                ("GET", f"/v1/jobs/{JOB_ID}", f"Bearer {JOB_TOKEN}"),
            ],
        )

    def test_the_exit_code_of_the_run_is_the_exit_code_of_the_step(self):
        for code in range(1, 12):
            with self.subTest(code=code):
                self.start([ACCEPTED, finished(code, FAIL_JUNIT)])
                self.assertEqual(self.run_main(), code)
                self.assertEqual(self.outputs()["exit-code"], str(code))

    def test_a_failed_check_gives_one_error_line(self):
        self.start([ACCEPTED, finished(1, FAIL_JUNIT)])
        self.run_main()
        self.assertIn("::error::crc32: the app reported status=FAIL\n", self.stdout)

    def test_a_preflight_refusal_gives_its_sentence_with_an_empty_log(self):
        self.start([ACCEPTED, finished(2, PREFLIGHT_JUNIT, b"")])
        self.assertEqual(self.run_main(), 2)
        self.assertIn("::error::the TBF needs Tock 2.4; build it for 2.3\n", self.stdout)

    def test_an_expected_failure_passes_with_a_note_and_no_error_line(self):
        self.start([ACCEPTED, finished(1, FAIL_JUNIT)])
        os.environ["EXPECTED_EXIT"] = "1"
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(self.outputs()["exit-code"], "1")
        self.assertIn("This run expected exit 1 and got it.", self.summary())
        self.assertNotIn("::error::", self.stdout)

    def test_a_pass_that_expected_a_failure_is_exit_28(self):
        self.start([ACCEPTED, finished(0)])
        os.environ["EXPECTED_EXIT"] = "1"
        self.assertEqual(self.run_main(), submit.UNEXPECTED_PASS)
        self.assertEqual(self.outputs()["exit-code"], "0")

    def test_another_failure_than_the_expected_one_keeps_its_code(self):
        self.start([ACCEPTED, finished(9)])
        os.environ["EXPECTED_EXIT"] = "1"
        self.assertEqual(self.run_main(), 9)

    def test_each_refusal_has_its_exit_code(self):
        cases = {
            "queue_full": (503, submit.QUEUE_FULL),
            "board_offline": (503, submit.BOARD_NOT_AVAILABLE),
            "board_missing": (503, submit.BOARD_NOT_AVAILABLE),
            "board_paused": (503, submit.BOARD_NOT_AVAILABLE),
            "request_invalid": (400, submit.REFUSED),
            "tab_missing": (400, submit.REFUSED),
            "tab_too_large": (413, submit.REFUSED),
            "token_invalid": (401, submit.REFUSED),
            "token_reused": (401, submit.REFUSED),
            "board_unknown": (404, submit.REFUSED),
            "owner_denied": (403, submit.REFUSED),
            "account_unknown": (403, submit.REFUSED),
            "account_too_young": (403, submit.REFUSED),
            "daily_limit": (429, submit.DAILY_LIMIT),
        }
        for error, (status, code) in cases.items():
            with self.subTest(error=error):
                self.start([refusal(status, error, "the sentence")] * 30)
                self.assertEqual(self.run_main(), code)
                self.assertEqual(self.outputs()["exit-code"], str(code))
                self.assertIn("the sentence", self.stdout)
                self.assertIn("NOT RUN", self.summary())

    def test_a_short_outage_submits_again_with_a_new_token(self):
        self.start(
            [
                refusal(503, "board_offline"),
                refusal(503, "board_missing"),
                refusal(503, "queue_full"),
                ACCEPTED,
                finished(0),
            ]
        )
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(self.clock.sleeps[:3], [30, 30, 30])
        self.assertEqual(self.service.requests[3][2], "Bearer token-4")

    def test_a_long_outage_stops_at_the_board_wait_limit(self):
        self.start([refusal(503, "board_offline")] * 30)
        self.assertEqual(self.run_main(), submit.BOARD_NOT_AVAILABLE)
        self.assertLess(self.clock.now, submit.BOARD_WAIT_LIMIT_SECONDS)

    def test_a_paused_board_does_not_wait(self):
        self.start([refusal(503, "board_paused")])
        self.assertEqual(self.run_main(), submit.BOARD_NOT_AVAILABLE)
        self.assertEqual(self.clock.sleeps, [])

    def test_owner_busy_submits_again_with_a_new_token(self):
        self.start([refusal(409, "owner_busy"), refusal(409, "owner_busy"), ACCEPTED, finished(0)])
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(
            [request[2] for request in self.service.requests[:3]],
            ["Bearer token-1", "Bearer token-2", "Bearer token-3"],
        )
        self.assertEqual(self.clock.sleeps[:2], [30, 30])

    def test_owner_busy_stops_at_the_wait_limit(self):
        self.start([refusal(409, "owner_busy")] * 200)
        self.assertEqual(self.run_main(), submit.WAIT_LIMIT)
        self.assertLess(self.clock.now, submit.WAIT_LIMIT_SECONDS)

    def test_a_transient_failure_submits_again(self):
        for answer in (
            refusal(503, "key_set_unavailable"),
            refusal(503, "account_lookup_failed"),
            refusal(500, "internal_error"),
            (502, b"<html>cloudflare</html>"),
        ):
            with self.subTest(answer=answer):
                self.start([answer, ACCEPTED, finished(0)])
                self.assertEqual(self.run_main(), 0)

    def test_a_service_that_stays_down_is_exit_26(self):
        self.start([(502, b"<html>cloudflare</html>")] * 3)
        self.assertEqual(self.run_main(), submit.SERVICE_NOT_AVAILABLE)
        self.assertEqual(len(self.service.requests), submit.SUBMIT_ATTEMPTS)

    def test_the_poll_obeys_the_service_and_prints_each_change_once(self):
        self.start([ACCEPTED, QUEUED, QUEUED, RUNNING, finished(0)])
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(self.clock.sleeps, [10, 20, 20, 10])
        self.assertEqual(self.stdout.count("position 2"), 1)
        self.assertEqual(self.stdout.count("0b0d9c8e: running"), 1)

    def test_the_wait_limit_removes_a_queued_job(self):
        self.start([ACCEPTED] + [QUEUED] * 200)
        self.assertEqual(self.run_main(), submit.WAIT_LIMIT)
        self.assertEqual(self.service.requests[-1][0], "DELETE")

    def test_the_wait_limit_leaves_a_job_that_reached_the_board(self):
        self.start(
            [ACCEPTED] + [QUEUED] * 181 + [RUNNING, finished(0)],
            delete_answer=refusal(409, "job_running"),
        )
        self.assertEqual(self.run_main(), 0)

    def test_a_job_that_stays_on_the_board_stops_at_the_hard_limit(self):
        self.start([ACCEPTED] + [RUNNING] * 500)
        self.assertEqual(self.run_main(), submit.HOST_LOST)
        self.assertLess(self.clock.now, submit.WAIT_LIMIT_SECONDS + submit.RUNNING_LIMIT_SECONDS + 20)

    def test_a_result_that_is_not_valid_is_exit_26(self):
        status, answer = finished(0)
        del answer["result"]["log_base64"]
        self.start([ACCEPTED, (status, answer)])
        self.assertEqual(self.run_main(), submit.SERVICE_NOT_AVAILABLE)

    def test_a_lost_host_is_exit_23(self):
        self.start([ACCEPTED, ended("host_lost")])
        self.assertEqual(self.run_main(), submit.HOST_LOST)

    def test_a_job_with_no_result_is_exit_25(self):
        for state in ("expired", "cancelled"):
            with self.subTest(state=state):
                self.start([ACCEPTED, ended(state)])
                self.assertEqual(self.run_main(), submit.JOB_ENDED_WITH_NO_RESULT)

    def test_an_unknown_job_is_exit_25(self):
        self.start([ACCEPTED, refusal(404, "job_unknown")])
        self.assertEqual(self.run_main(), submit.JOB_ENDED_WITH_NO_RESULT)

    def test_a_failed_poll_tries_again(self):
        down = (502, b"<html>cloudflare</html>")
        self.start([ACCEPTED, down, down, RUNNING, finished(0)])
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(self.clock.sleeps[:3], [10, 15, 15])

    def test_a_poll_that_stays_down_is_exit_26(self):
        self.start([ACCEPTED] + [(502, b"<html>cloudflare</html>")] * submit.POLL_ATTEMPTS)
        self.assertEqual(self.run_main(), submit.SERVICE_NOT_AVAILABLE)

    def test_a_cancel_removes_the_job(self):
        self.start([ACCEPTED])
        submit.sleep = mock.Mock(side_effect=submit.Cancelled)
        self.assertEqual(self.run_main(), submit.JOB_ENDED_WITH_NO_RESULT)
        self.assertEqual(self.service.requests[-1][0], "DELETE")

    def test_no_oidc_token_is_exit_27(self):
        self.start([])
        del os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"]
        self.assertEqual(self.run_main(), submit.WORKFLOW_INCORRECT)
        self.assertIn("id-token: write", self.stdout)

    def test_no_board_is_exit_27(self):
        self.start([])
        os.environ["BOARD"] = ""
        self.assertEqual(self.run_main(), submit.WORKFLOW_INCORRECT)

    def test_no_tab_is_exit_27(self):
        self.start([])
        self.app.unlink()
        self.assertEqual(self.run_main(), submit.WORKFLOW_INCORRECT)

    def test_an_expected_exit_outside_the_harness_codes_is_exit_27(self):
        for value in ("12", "20", "one", "", "-1"):
            with self.subTest(value=value):
                self.start([])
                os.environ["EXPECTED_EXIT"] = value
                self.assertEqual(self.run_main(), submit.WORKFLOW_INCORRECT)

    def test_a_log_cannot_send_workflow_commands(self):
        hostile = b"::add-mask::secret\n\x1b[31mred\x1b[0m\n\x1eHILSHARE_KERNEL_BOOT\r\n"
        self.start([ACCEPTED, finished(0, log=hostile)])
        self.run_main()
        lines = self.stdout.splitlines()
        start = next(index for index, line in enumerate(lines) if line.startswith("::stop-commands::"))
        guard = lines[start].split("::")[2]
        self.assertIn("::add-mask::secret", lines[start + 1 : lines.index(f"::{guard}::")])
        self.assertNotIn("\x1b", self.stdout)
        self.assertIn("␞HILSHARE_KERNEL_BOOT", self.stdout)

    def test_a_workflow_command_value_is_escaped(self):
        self.assertEqual(submit.escape_data("50%\r\nnext"), "50%25%0D%0Anext")

    def test_junit_that_does_not_parse_gives_no_sentences(self):
        self.assertEqual(submit.junit_errors(""), [])

    def test_the_artifact_name_has_only_safe_characters(self):
        self.start([ACCEPTED, finished(0)])
        os.environ["APP"] = str(self.directory / "my app:v1.tab")
        Path(os.environ["APP"]).write_bytes(b"tab")
        self.run_main()
        self.assertEqual(self.outputs()["artifact-name"], "hilshare-my-app-v1-nucleo-f411re")


if __name__ == "__main__":
    unittest.main()
