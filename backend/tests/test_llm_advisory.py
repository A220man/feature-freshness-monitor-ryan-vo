import unittest,json
import httpx
from app.services.llm_advisory import LLMSettings,advise

FACTS={"feature_set":"risk","healthy":False,"coverage":0.5,"partitions":[{"partition":"eu","status":"missing","private_record":"never transmit"}],"api_key":"never transmit"}
class AdapterTests(unittest.IsolatedAsyncioTestCase):
 async def test_all_protocols_send_minimized_evidence_and_parse_text(self):
  fixtures={"openai":('/chat/completions',{"choices":[{"message":{"content":"Check ingestion."}}]}),"compatible":('/chat/completions',{"choices":[{"message":{"content":"Check ingestion."}}]}),"anthropic":('/messages',{"content":[{"type":"text","text":"Check ingestion."}]}),"gemini":('/models/selected:generateContent',{"candidates":[{"content":{"parts":[{"text":"Check ingestion."}]}}]}),"ollama":('/api/chat',{"message":{"content":"Check ingestion."}})}
  for provider,(path,payload) in fixtures.items():
   with self.subTest(provider=provider):
    def handler(request):
     self.assertEqual(request.url.path,path);self.assertNotIn('never transmit',request.content.decode())
     body=json.loads(request.content);self.assertNotIn('tools',body)
     if provider=='gemini':self.assertEqual(request.headers['x-goog-api-key'],'synthetic-fixture')
     else:self.assertEqual(request.headers['authorization'],'Bearer synthetic-fixture')
     return httpx.Response(200,json=payload)
    result=await advise(LLMSettings(provider,'selected','https://example.test','synthetic-fixture'),FACTS,httpx.MockTransport(handler))
    self.assertEqual(result.status,'available');self.assertEqual(result.text,'Check ingestion.');self.assertTrue(result.advisory_only)
 async def test_disabled_does_not_call_provider(self):
  def forbidden(request):raise AssertionError('No network expected')
  result=await advise(None,FACTS,httpx.MockTransport(forbidden));self.assertEqual(result.status,'disabled')
 async def test_rate_limit_and_private_provider_error_not_exposed(self):
  for code,status in [(429,'rate_limited'),(401,'unavailable'),(500,'unavailable'),(302,'unavailable')]:
   with self.subTest(code=code):
    result=await advise(LLMSettings('openai','selected','https://example.test','synthetic-fixture'),FACTS,httpx.MockTransport(lambda request:httpx.Response(code,text='secret provider details',headers={'Location':'https://other.test'})))
    self.assertEqual(result.status,status);self.assertNotIn('secret',result.text)
 async def test_malformed_or_oversized_response_is_safe_failure(self):
  for content in [b'not-json',b'x'*262145,b'{"choices":[]}']:
   result=await advise(LLMSettings('openai','selected','https://example.test','synthetic-fixture'),FACTS,httpx.MockTransport(lambda request:httpx.Response(200,content=content)))
   self.assertEqual(result.status,'unavailable')
 async def test_no_stored_facts_mutated_and_echoed_credential_redacted(self):
  original=json.dumps(FACTS,sort_keys=True)
  result=await advise(LLMSettings('openai','selected','https://example.test','synthetic-fixture'),FACTS,httpx.MockTransport(lambda request:httpx.Response(200,json={'choices':[{'message':{'content':'synthetic-fixture'}}]})))
  self.assertEqual(result.text,'[REDACTED]');self.assertEqual(json.dumps(FACTS,sort_keys=True),original)
 def test_remote_cleartext_and_url_credentials_rejected(self):
  for url in ['http://example.test','https://user:password@example.test','https://example.test?key=secret']:
   with self.assertRaises(ValueError):LLMSettings('compatible','selected',url)
  self.assertNotIn('synthetic-fixture',repr(LLMSettings('compatible','selected','http://localhost:11434','synthetic-fixture')))
