export interface JobStatus {
  job_id: string;
  status: 'pending' | 'processing' | 'completed' | 'failed';
  progress: number;
  message: string;
  error?: string;
}

export interface SlideConfig {
  model_name_t: string;
  model_name_v: string;
}

export interface UploadedFiles {
  pdf_file: File | null;
}
