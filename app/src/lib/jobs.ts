// What the helper's `jobs` and `job` commands return, as the Jobs screen and the job card use it.

export interface JobRow {
  id: string;
  title: string;
  url: string;
  pay: string;
  skills: string;
  published: string | null;
  first_seen: string;
  score: number | null;
  section: string | null;
  matched: string[];
  label: number | null;
}

export interface JobDetails extends JobRow {
  description: string;
}
