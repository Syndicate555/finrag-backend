-- Documents table
create table if not exists documents (
    id uuid primary key default gen_random_uuid(),
    filename text not null,
    blob_url text,
    status text not null default 'pending' check (status in ('pending', 'processing', 'ready', 'failed')),
    page_count integer,
    sections jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

-- Document sections table
create table if not exists document_sections (
    id uuid primary key default gen_random_uuid(),
    document_id uuid not null references documents(id) on delete cascade,
    heading text not null,
    level integer not null check (level in (1, 2)),
    start_page integer not null,
    end_page integer not null,
    parent_section_id uuid references document_sections(id) on delete set null,
    created_at timestamptz not null default now()
);

create index if not exists idx_document_sections_document_id on document_sections(document_id);

-- Threads table
create table if not exists threads (
    id uuid primary key default gen_random_uuid(),
    title text,
    document_id uuid references documents(id) on delete set null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

-- Messages table
create table if not exists messages (
    id uuid primary key default gen_random_uuid(),
    thread_id uuid not null references threads(id) on delete cascade,
    role text not null check (role in ('user', 'assistant')),
    content text not null,
    citations jsonb,
    clarification_chips jsonb,
    message_type text check (message_type in ('kb', 'general', 'clarification')),
    created_at timestamptz not null default now()
);

create index if not exists idx_messages_thread_id on messages(thread_id);

-- Updated_at trigger function
create or replace function update_updated_at()
returns trigger as $$
begin
    new.updated_at = now();
    return new;
end;
$$ language plpgsql;

create or replace trigger documents_updated_at
    before update on documents
    for each row execute function update_updated_at();

create or replace trigger threads_updated_at
    before update on threads
    for each row execute function update_updated_at();

-- Message feedback table
create table if not exists message_feedback (
    message_id uuid primary key references messages(id) on delete cascade,
    signal smallint not null check (signal in (-1, 1)),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create or replace trigger message_feedback_updated_at
    before update on message_feedback
    for each row execute function update_updated_at();
