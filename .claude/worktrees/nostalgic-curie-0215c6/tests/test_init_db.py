from __future__ import annotations

from contextlib import nullcontext
import unittest
from unittest.mock import call, patch

from app.services.embeddings import EmbeddingConfigurationError, EmbeddingError
from scripts import init_db as init_db_script


class TestInitDbScript(unittest.TestCase):
    def test_main_initializes_database_without_reindexing(self) -> None:
        args = init_db_script.argparse.Namespace(reindex_embeddings=False)

        with patch("scripts.init_db.parse_args", return_value=args):
            with patch("scripts.init_db.load_dotenv") as load_dotenv_mock:
                with patch("scripts.init_db.init_db") as init_db_mock:
                    with patch("builtins.print") as print_mock:
                        init_db_script.main()

        load_dotenv_mock.assert_called_once()
        self.assertTrue(load_dotenv_mock.call_args.args[0].name.endswith(".env"))
        self.assertTrue(load_dotenv_mock.call_args.kwargs["override"])
        init_db_mock.assert_called_once_with()
        print_mock.assert_called_once_with("Database initialized.")

    def test_main_reindexes_embeddings_when_requested(self) -> None:
        args = init_db_script.argparse.Namespace(reindex_embeddings=True)
        connection = object()

        with patch("scripts.init_db.parse_args", return_value=args):
            with patch("scripts.init_db.load_dotenv"):
                with patch("scripts.init_db.init_db") as init_db_mock:
                    with patch(
                        "scripts.init_db.connection_context",
                        return_value=nullcontext(connection),
                    ) as connection_context_mock:
                        with patch(
                            "scripts.init_db.reindex_all_embeddings",
                            return_value=(3, "Rebuilt embeddings for 3 entries."),
                        ) as reindex_mock:
                            with patch("builtins.print") as print_mock:
                                init_db_script.main()

        init_db_mock.assert_called_once_with()
        connection_context_mock.assert_called_once_with()
        reindex_mock.assert_called_once_with(connection)
        self.assertEqual(
            print_mock.call_args_list,
            [
                call("Database initialized."),
                call("Rebuilt embeddings for 3 entries."),
                call("Indexed entries: 3"),
            ],
        )

    def test_main_exits_with_configuration_error_message(self) -> None:
        args = init_db_script.argparse.Namespace(reindex_embeddings=True)

        with patch("scripts.init_db.parse_args", return_value=args):
            with patch("scripts.init_db.load_dotenv"):
                with patch("scripts.init_db.init_db"):
                    with patch(
                        "scripts.init_db.connection_context",
                        return_value=nullcontext(object()),
                    ):
                        with patch(
                            "scripts.init_db.reindex_all_embeddings",
                            side_effect=EmbeddingConfigurationError(
                                "Missing embedding settings."
                            ),
                        ):
                            with self.assertRaises(SystemExit) as context:
                                init_db_script.main()

        self.assertEqual(str(context.exception), "Missing embedding settings.")

    def test_main_exits_with_wrapped_embedding_error_message(self) -> None:
        args = init_db_script.argparse.Namespace(reindex_embeddings=True)

        with patch("scripts.init_db.parse_args", return_value=args):
            with patch("scripts.init_db.load_dotenv"):
                with patch("scripts.init_db.init_db"):
                    with patch(
                        "scripts.init_db.connection_context",
                        return_value=nullcontext(object()),
                    ):
                        with patch(
                            "scripts.init_db.reindex_all_embeddings",
                            side_effect=EmbeddingError("provider unavailable"),
                        ):
                            with self.assertRaises(SystemExit) as context:
                                init_db_script.main()

        self.assertEqual(
            str(context.exception),
            "Embedding reindex failed: provider unavailable",
        )


if __name__ == "__main__":
    unittest.main()
