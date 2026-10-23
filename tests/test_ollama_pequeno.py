import _entorno
import os
import unittest
from unittest.mock import Mock, patch

from core import llm_local


class OllamaPequeno(unittest.TestCase):
    def test_qwen3_sin_thinking_por_defecto_y_opcion_manual(self):
        with patch.object(llm_local, '_MODELO', 'qwen3:1.7b'), \
             patch.dict(os.environ, {'LARA_OLLAMA_THINK': '0'}):
            self.assertEqual(llm_local._opciones_thinking(), {'think': False})
            os.environ['LARA_OLLAMA_THINK'] = '1'
            self.assertEqual(llm_local._opciones_thinking(), {'think': True})

    def test_modelos_anteriores_no_reciben_opcion_incompatible(self):
        with patch.object(llm_local, '_MODELO', 'qwen2.5:3b-instruct'):
            self.assertEqual(llm_local._opciones_thinking(), {})

    def test_chat_qwen3_envia_think_y_formato_json(self):
        http = Mock()
        http.post.return_value.status_code = 200
        http.post.return_value.json.return_value = {'message': {'content': '{"ok":true}'}}
        with patch.object(llm_local, '_MODELO', 'qwen3:1.7b'), \
             patch.object(llm_local, 'disponible', return_value=True), \
             patch.object(llm_local, '_http', return_value=http), \
             patch.dict(os.environ, {'LARA_OLLAMA_THINK': '0'}):
            self.assertEqual(llm_local.chat([{'role': 'user', 'content': 'JSON'}], json_mode=True), '{"ok":true}')
        payload = http.post.call_args.kwargs['json']
        self.assertFalse(payload['think'])
        self.assertEqual(payload['format'], 'json')


if __name__ == '__main__':
    unittest.main()
