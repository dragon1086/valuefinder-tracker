import { NextResponse } from 'next/server';
import { readFileSync } from 'fs';
import { join } from 'path';

export const dynamic = 'force-static';

export function GET() {
  try {
    const filePath = join(process.cwd(), 'public', 'reports.json');
    const raw = readFileSync(filePath, 'utf-8');
    const data = JSON.parse(raw);
    return NextResponse.json(data);
  } catch {
    return NextResponse.json(
      { error: 'reports.json not found', updated_at: null, reports: [] },
      { status: 500 }
    );
  }
}
