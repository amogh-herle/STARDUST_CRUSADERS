# Supabase Setup Guide for CIDECODE

## Step 1: Create Supabase Project

1. Go to https://supabase.com
2. Click "Start your project"
3. Sign in with GitHub (or create account)
4. Click "New Project"
5. Fill in:
   - Project name: `cidecode-bank-analysis`
   - Database password: (generate strong password and save it)
   - Region: Choose closest to you
   - Pricing plan: Free tier is fine for development
6. Click "Create new project"
7. Wait 2-3 minutes for project to provision

## Step 2: Get API Credentials

Once project is ready:

1. Click on "Settings" (gear icon) in left sidebar
2. Click "API" under Project Settings
3. Copy these values:
   - **Project URL** (looks like: `https://xxxxx.supabase.co`)
   - **anon public** key (under "Project API keys")

## Step 3: Create Database Tables

1. Click "SQL Editor" in left sidebar
2. Click "+ New query"
3. Paste this SQL and click "Run":

```sql
-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Create profiles table (extends Supabase auth.users)
CREATE TABLE IF NOT EXISTS public.profiles (
  id UUID REFERENCES auth.users(id) ON DELETE CASCADE PRIMARY KEY,
  email TEXT UNIQUE NOT NULL,
  full_name TEXT,
  role TEXT DEFAULT 'investigator',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Create cases table
CREATE TABLE IF NOT EXISTS public.cases (
  id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  created_by UUID REFERENCES public.profiles(id) ON DELETE CASCADE NOT NULL,
  case_name TEXT NOT NULL,
  case_number TEXT UNIQUE NOT NULL,
  status TEXT DEFAULT 'open' CHECK (status IN ('open', 'in_progress', 'closed', 'archived')),
  priority TEXT DEFAULT 'medium' CHECK (priority IN ('low', 'medium', 'high', 'critical')),
  description TEXT,
  assigned_to UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  closed_at TIMESTAMPTZ
);

-- Create case_uploads table (links uploads to cases)
CREATE TABLE IF NOT EXISTS public.case_uploads (
  id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  case_id UUID REFERENCES public.cases(id) ON DELETE CASCADE NOT NULL,
  upload_id TEXT NOT NULL,
  file_name TEXT NOT NULL,
  uploaded_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL NOT NULL,
  upload_date TIMESTAMPTZ DEFAULT NOW(),
  analytics_status TEXT DEFAULT 'pending' CHECK (analytics_status IN ('pending', 'processing', 'completed', 'failed'))
);

-- Create case_notes table
CREATE TABLE IF NOT EXISTS public.case_notes (
  id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
  case_id UUID REFERENCES public.cases(id) ON DELETE CASCADE NOT NULL,
  created_by UUID REFERENCES public.profiles(id) ON DELETE CASCADE NOT NULL,
  note_text TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Enable Row Level Security
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.cases ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.case_uploads ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.case_notes ENABLE ROW LEVEL SECURITY;

-- RLS Policies for profiles (users can read all profiles, but only update their own)
CREATE POLICY "Public profiles are viewable by authenticated users"
  ON public.profiles FOR SELECT
  TO authenticated
  USING (true);

CREATE POLICY "Users can update own profile"
  ON public.profiles FOR UPDATE
  TO authenticated
  USING (auth.uid() = id);

-- RLS Policies for cases (users can view all cases they created or are assigned to)
CREATE POLICY "Users can view their own cases"
  ON public.cases FOR SELECT
  TO authenticated
  USING (created_by = auth.uid() OR assigned_to = auth.uid());

CREATE POLICY "Users can create cases"
  ON public.cases FOR INSERT
  TO authenticated
  WITH CHECK (created_by = auth.uid());

CREATE POLICY "Users can update their own cases"
  ON public.cases FOR UPDATE
  TO authenticated
  USING (created_by = auth.uid() OR assigned_to = auth.uid());

-- RLS Policies for case_uploads
CREATE POLICY "Users can view uploads for their cases"
  ON public.case_uploads FOR SELECT
  TO authenticated
  USING (
    EXISTS (
      SELECT 1 FROM public.cases
      WHERE cases.id = case_uploads.case_id
      AND (cases.created_by = auth.uid() OR cases.assigned_to = auth.uid())
    )
  );

CREATE POLICY "Users can create uploads for their cases"
  ON public.case_uploads FOR INSERT
  TO authenticated
  WITH CHECK (
    EXISTS (
      SELECT 1 FROM public.cases
      WHERE cases.id = case_uploads.case_id
      AND (cases.created_by = auth.uid() OR cases.assigned_to = auth.uid())
    )
  );

-- RLS Policies for case_notes
CREATE POLICY "Users can view notes for their cases"
  ON public.case_notes FOR SELECT
  TO authenticated
  USING (
    EXISTS (
      SELECT 1 FROM public.cases
      WHERE cases.id = case_notes.case_id
      AND (cases.created_by = auth.uid() OR cases.assigned_to = auth.uid())
    )
  );

CREATE POLICY "Users can create notes for their cases"
  ON public.case_notes FOR INSERT
  TO authenticated
  WITH CHECK (
    EXISTS (
      SELECT 1 FROM public.cases
      WHERE cases.id = case_notes.case_id
      AND (cases.created_by = auth.uid() OR cases.assigned_to = auth.uid())
    ) AND created_by = auth.uid()
  );

-- Create function to automatically create profile on signup
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO public.profiles (id, email, full_name)
  VALUES (
    NEW.id,
    NEW.email,
    COALESCE(NEW.raw_user_meta_data->>'full_name', NEW.email)
  );
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Trigger to create profile on user signup
DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

-- Create indexes for better performance
CREATE INDEX IF NOT EXISTS idx_cases_created_by ON public.cases(created_by);
CREATE INDEX IF NOT EXISTS idx_cases_assigned_to ON public.cases(assigned_to);
CREATE INDEX IF NOT EXISTS idx_cases_status ON public.cases(status);
CREATE INDEX IF NOT EXISTS idx_case_uploads_case_id ON public.case_uploads(case_id);
CREATE INDEX IF NOT EXISTS idx_case_notes_case_id ON public.case_notes(case_id);
```

4. You should see "Success. No rows returned" — this is correct!

## Step 4: Configure Email Authentication

1. Go to "Authentication" in left sidebar
2. Click "Providers"
3. Make sure "Email" is enabled
4. Under "Email Auth" settings:
   - Enable "Confirm email" (recommended for production)
   - For development, you can disable it to skip email confirmation

## Step 5: Update Frontend Environment Variables

1. In your frontend project, open `.env` file
2. Add these variables (replace with your actual values from Step 2):

```
NEXT_PUBLIC_SUPABASE_URL=https://xxxxx.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=your-anon-key-here
```

3. Restart your Next.js dev server after adding these

## Step 6: Test Authentication

1. Start your frontend: `npm run dev`
2. Go to http://localhost:3000
3. You should see the login page
4. Click "Register" and create a test account
5. Log in with those credentials

## Database Schema Overview

### Tables Created:

1. **profiles** — User profiles (extends Supabase auth.users)
   - Stores investigator information
   - One profile per auth user (created automatically)

2. **cases** — Investigation cases
   - Each case has a creator and optional assignee
   - Statuses: open, in_progress, closed, archived
   - Priorities: low, medium, high, critical

3. **case_uploads** — Links uploaded bank statements to cases
   - Tracks which files belong to which case
   - Stores analytics status

4. **case_notes** — Notes/comments on cases
   - Investigators can add notes to cases they're working on

### Security (Row Level Security)
- Users can only see their own cases (created by them or assigned to them)
- Users can only see uploads and notes for their cases
- Profiles are viewable by all authenticated users (for assignment purposes)

## Troubleshooting

### "Invalid API key" error
- Double-check you copied the **anon public** key (not service_role key)
- Make sure there are no extra spaces in the .env file

### Can't create account
- Check if email confirmation is required in Supabase settings
- Check browser console for errors
- Verify Supabase project URL is correct

### Database queries failing
- Make sure SQL in Step 3 ran successfully
- Check "Database" → "Tables" to verify tables exist
- Check RLS policies are enabled

## Next Steps

Once setup is complete, you can:
- Create cases from the frontend
- Upload bank statements linked to cases
- View all your cases in the profile menu
- Share cases with other investigators (assign feature)
