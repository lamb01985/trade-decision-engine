import type { ReactNode } from 'react';
export function StatusPanel({kind='neutral',title,children}:{kind?:string;title:string;children?:ReactNode}){return <section className={`status-panel ${kind}`} role={kind==='error'?'alert':'status'}><strong>{title}</strong>{children&&<p>{children}</p>}</section>}
