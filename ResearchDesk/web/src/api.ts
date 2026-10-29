let csrf = '';
export async function api(path: string, options: any = {}) {
  const response = await fetch('/api' + path, {credentials:'same-origin', ...options,
    headers:{...(options.body && !(options.body instanceof FormData) ? {'Content-Type':'application/json'}:{}),
      ...(csrf ? {'X-CSRF-Token':csrf}:{}), ...options.headers}});
  if (!response.ok) {
    let message = `请求失败 (${response.status})`;
    try { const data = await response.json(); message = typeof data.detail === 'string' ? data.detail : message; } catch {}
    throw new Error(message);
  }
  const value = await response.json();
  if (path === '/bootstrap') csrf = value.csrf;
  return value;
}
export const post = (path:string, body:any={}) => api(path,{method:'POST',body:JSON.stringify(body)});
export const patch = (path:string, body:any) => api(path,{method:'PATCH',body:JSON.stringify(body)});
