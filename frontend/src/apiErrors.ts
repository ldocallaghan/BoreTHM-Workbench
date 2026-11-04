type Issue={message?:unknown;msg?:unknown;path?:unknown;loc?:unknown};
export function formatApiError(payload:unknown):string {
  const detail=payload&&typeof payload==='object'&&'detail' in payload?(payload as {detail:unknown}).detail:payload;
  if(typeof detail==='string')return detail;
  const issues=Array.isArray(detail)?detail:[detail];
  return issues.map(value=>{
    if(typeof value==='string')return value;
    if(value&&typeof value==='object'){
      const issue=value as Issue;
      const message=issue.message??issue.msg;
      const path=typeof issue.path==='string'?issue.path:Array.isArray(issue.loc)?'/'+issue.loc.filter(x=>x!=='body').join('/'):'';
      if(typeof message==='string')return `${path?path+': ':''}${message}`;
    }
    return 'The request could not be completed. Check the selected file or inputs.';
  }).join('\n');
}
