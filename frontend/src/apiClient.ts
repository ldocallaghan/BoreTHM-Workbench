import {formatApiError} from './apiErrors';
export async function api<T>(path:string,options?:RequestInit):Promise<T>{
  const response=await fetch('/api/v1'+path,{...options,headers:{'Content-Type':'application/json',...options?.headers}});
  const value=await response.json();
  if(!response.ok)throw new Error(formatApiError(value));
  return value as T;
}
