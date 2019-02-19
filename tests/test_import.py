from dvc.utils.compat import str

import os
from uuid import uuid4

from dvc import logger
from dvc.utils.compat import urljoin
from dvc.exceptions import DvcException
from dvc.main import main
from mock import patch, mock_open, call
from tests.basic_env import TestDvc
from tests.utils.httpd import StaticFileServer
from tests.utils.logger import MockLoggerHandlers


from dvc.utils.compat import StringIO


class TestCmdImport(TestDvc):
    def test(self):
        ret = main(["import", self.FOO, "import"])
        self.assertEqual(ret, 0)
        self.assertTrue(os.path.exists("import.dvc"))

        ret = main(["import", "non-existing-file", "import"])
        self.assertNotEqual(ret, 0)

    def test_unsupported(self):
        ret = main(["import", "unsupported://path", "import_unsupported"])
        self.assertNotEqual(ret, 0)


class TestDefaultOutput(TestDvc):
    def test(self):
        tmpdir = self.mkdtemp()
        filename = str(uuid4())
        tmpfile = os.path.join(tmpdir, filename)

        with open(tmpfile, "w") as fd:
            fd.write("content")

        ret = main(["import", tmpfile])
        self.assertEqual(ret, 0)
        self.assertTrue(os.path.exists(filename))
        self.assertEqual(open(filename).read(), "content")


class TestFailedImportMessage(TestDvc):
    color_patch = patch.object(logger, "colorize", new=lambda x, color="": x)

    def test(self):
        self.color_patch.start()
        self._test()
        self.color_patch.stop()

    @patch("dvc.command.imp.urlparse")
    def _test(self, imp_urlparse_patch):
        with MockLoggerHandlers(logger.logger):
            logger.logger.handlers[1].stream = StringIO()
            page_address = "http://somesite.com/file_name"

            def dvc_exception(*args, **kwargs):
                raise DvcException("message")

            imp_urlparse_patch.side_effect = dvc_exception
            main(["import", page_address])
            self.assertIn(
                "Error: failed to import "
                "http://somesite.com/file_name. You could also try "
                "downloading it manually and adding it with `dvc add` "
                "command.",
                logger.logger.handlers[1].stream.getvalue(),
            )


class TestInterruptedDownload(TestDvc):
    @property
    def remote(self):
        return "http://localhost:8000/"

    def _prepare_interrupted_download(self):
        import_url = urljoin(self.remote, self.FOO)
        import_output = "imported_file"
        tmp_file_name = import_output + ".part"
        tmp_file_path = os.path.realpath(
            os.path.join(self._root_dir, tmp_file_name)
        )
        self._import_with_interrupt(import_output, import_url)
        self.assertTrue(os.path.exists(tmp_file_name))
        self.assertFalse(os.path.exists(import_output))
        return import_output, import_url, tmp_file_path

    def _import_with_interrupt(self, import_output, import_url):
        def interrupting_generator():
            yield self.FOO[0].encode("utf8")
            raise KeyboardInterrupt

        with patch(
            "requests.models.Response.iter_content",
            return_value=interrupting_generator(),
        ):
            with patch(
                "dvc.remote.http.RemoteHTTP._content_length", return_value=3
            ):
                result = main(["import", import_url, import_output])
                self.assertEqual(result, 252)


class TestShouldResumeDownload(TestInterruptedDownload):
    @patch("dvc.remote.http.RemoteHTTP.CHUNK_SIZE", 1)
    def test(self):
        with StaticFileServer():
            import_output, import_url, tmp_file_path = (
                self._prepare_interrupted_download()
            )

            m = mock_open()
            with patch("dvc.remote.http.open", m):
                result = main(
                    ["import", "--resume", import_url, import_output]
                )
                self.assertEqual(result, 0)
        m.assert_called_once_with(tmp_file_path, "ab")
        m_handle = m()
        expected_calls = [call(b"o"), call(b"o")]
        m_handle.write.assert_has_calls(expected_calls, any_order=False)


class TestShouldNotResumeDownload(TestInterruptedDownload):
    @patch("dvc.remote.http.RemoteHTTP.CHUNK_SIZE", 1)
    def test(self):
        with StaticFileServer():
            import_output, import_url, tmp_file_path = (
                self._prepare_interrupted_download()
            )

            m = mock_open()
            with patch("dvc.remote.http.open", m):
                result = main(["import", import_url, import_output])
                self.assertEqual(result, 0)
        m.assert_called_once_with(tmp_file_path, "wb")
        m_handle = m()
        expected_calls = [call(b"f"), call(b"o"), call(b"o")]
        m_handle.write.assert_has_calls(expected_calls, any_order=False)
