import axios from 'axios';
import { JobStatus, SlideConfig, UploadedFiles } from './types';

// Configure API base via Vite env if needed:
//   VITE_API_BASE=http://localhost:8000
//   VITE_API_BASE=http://127.0.0.1:8000
const API_BASE = (import.meta as any).env?.VITE_API_BASE || 'http://localhost:8000';

const api = axios.create({ baseURL: API_BASE });

export const apiService = {
  async getModels(): Promise<string[]> {
    const res = await api.get('/models');
    return res.data.models;
  },

  async generateSlides(config: SlideConfig, files: UploadedFiles): Promise<JobStatus> {
    const form = new FormData();
    form.append('model_name_t', config.model_name_t);
    form.append('model_name_v', config.model_name_v);
    if (!files.pdf_file) throw new Error('pdf_file is required');
    form.append('pdf_file', files.pdf_file);

    const res = await api.post('/generate', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
    return res.data;
  },

  async getJobStatus(jobId: string): Promise<JobStatus> {
    const res = await api.get(`/status/${jobId}`);
    return res.data;
  },

  async getJobLogs(jobId: string): Promise<string[]> {
    const res = await api.get(`/logs/${jobId}`);
    return res.data.logs;
  },

  getDownloadUrl(jobId: string): string {
    return `${API_BASE}/download/${jobId}`;
  },
};
