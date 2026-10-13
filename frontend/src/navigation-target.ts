/** Validate the server projection before adopting its authenticated HTML. */
export function matchesNavigation(boot:any,requested:URL,finalURL:URL){
  if(finalURL.origin!==requested.origin||finalURL.pathname!==requested.pathname||finalURL.search!==requested.search)return false;
  if(boot?.contract_version!=='1'||!Number.isInteger(boot.http_status)||boot.http_status<200||boot.http_status>=400)return false;
  if(boot.page_kind==='report')return requested.pathname.startsWith('/report/')&&boot.data?.report?.url===requested.pathname+requested.search;
  if(boot.page_kind==='project')return requested.pathname==='/history'&&requested.searchParams.has('project')&&(boot.data?.projects||[]).some((p:any)=>p.repo===requested.searchParams.get('project'));
  return false;
}
