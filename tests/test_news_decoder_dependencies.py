"""No-network regression for the selectolax 1.0 import break."""
import importlib
import unittest
from unittest.mock import Mock, patch


class NewsDecoderDependenciesTest(unittest.TestCase):
    def test_decoder_public_import(self):
        from googlenewsdecoder import gnewsdecoder
        self.assertTrue(callable(gnewsdecoder))

    def test_legacy_parser_extracts_google_metadata(self):
        # Package __init__ imports this module, even when callers use gnewsdecoder.
        decoder = importlib.import_module("googlenewsdecoder.new_decoderv1")
        response = Mock()
        response.text = '<c-wiz><div jscontroller="x" data-n-a-sg="sig" data-n-a-ts="123"></div></c-wiz>'
        with patch.object(decoder.requests, "get", return_value=response):
            result = decoder.get_decoding_params("encoded-article")
        self.assertTrue(result["status"])
        self.assertEqual(result["signature"], "sig")
        self.assertEqual(result["timestamp"], "123")


if __name__ == "__main__":
    unittest.main()
