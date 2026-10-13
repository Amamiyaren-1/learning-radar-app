import assert from 'node:assert/strict';
import test from 'node:test';
import {matchesNavigation} from '../src/navigation-target.ts';
const origin='http://127.0.0.1:53641';
const url=new URL('/history?project=fixture-ui%2Fproject-041',origin);
const page={contract_version:'1',http_status:200,page_kind:'project',data:{projects:[{repo:'fixture-ui/project-041'}]}};
test('GET enhancement accepts the authenticated target identity, including encoded repos and unclassified archives',()=>{
  assert.equal(matchesNavigation(page,url,url),true);
  const empty=new URL('/history?project=',origin);
  assert.equal(matchesNavigation({...page,data:{projects:[{repo:''}]}},empty,empty),true);
  const report=new URL('/report/fixture-f4-100',origin);
  assert.equal(matchesNavigation({...page,page_kind:'report',data:{report:{url:report.pathname}}},report,report),true);
});
test('login, errors, wrong identities and unexpected redirects cannot become a reading panel',()=>{
  for(const invalid of [{...page,page_kind:'login'},{...page,http_status:403},{...page,http_status:undefined},{...page,contract_version:'2'},{...page,data:{projects:[{repo:'fixture-ui/project-042'}]}}])assert.equal(matchesNavigation(invalid,url,url),false);
  assert.equal(matchesNavigation(page,url,new URL('/login',origin)),false);
  assert.equal(matchesNavigation(page,url,new URL(url.pathname+url.search,'https://example.com')),false);
});
