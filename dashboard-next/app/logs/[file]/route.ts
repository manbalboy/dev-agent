import { NextRequest } from 'next/server';

const BACKEND_BASE = process.env.AGENTHUB_API_BASE ?? 'http://127.0.0.1:8321';

type RouteContext = {
  params: { file: string };
};

function buildTarget(fileName: string, search: string): string {
  const base = BACKEND_BASE.endsWith('/') ? BACKEND_BASE.slice(0, -1) : BACKEND_BASE;
  return `${base}/logs/${encodeURIComponent(fileName)}${search}`;
}

export async function GET(request: NextRequest, context: RouteContext): Promise<Response> {
  const target = buildTarget(context.params.file, request.nextUrl.search);
  const headers = new Headers(request.headers);
  headers.delete('host');
  headers.delete('connection');

  const upstream = await fetch(target, {
    method: 'GET',
    headers,
    redirect: 'manual',
    cache: 'no-store',
  });

  return new Response(upstream.body, {
    status: upstream.status,
    headers: upstream.headers,
  });
}
