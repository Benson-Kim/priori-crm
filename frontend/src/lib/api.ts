/**
 * Base API client for all HTTP communication.
 */

import { appConfig } from "@/lib/constants";
import type { PaginatedApiResponse } from "@/lib/types";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function handleResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));

    // Handle validation errors with detailed information
    if (body.details?.errors && Array.isArray(body.details.errors)) {
      const errorMessages = body.details.errors
        .map((err: any) => {
          const field = err.loc?.join(".") || "unknown";
          return `${field}: ${err.msg}`;
        })
        .join("; ");
      throw new ApiError(
        errorMessages || body.error || "Validation failed",
        response.status
      );
    }

    const message =
      body.detail ||
      body.error ||
      response.statusText ||
      "An unexpected error occurred";
    throw new ApiError(message, response.status);
  }

  // 204 No Content — return empty object
  if (response.status === 204) {
    return {} as T;
  }

  return response.json();
}

export async function apiGet<T>(
  path: string,
  params?: Record<string, string | number | boolean | undefined | null>
): Promise<T> {
  const url = new URL(path, appConfig.apiUrl);
  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== "") {
        url.searchParams.set(key, String(value));
      }
    });
  }
  const response = await fetch(url.toString());
  return handleResponse<T>(response);
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(new URL(path, appConfig.apiUrl).toString(), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handleResponse<T>(response);
}

export async function apiPut<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(new URL(path, appConfig.apiUrl).toString(), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handleResponse<T>(response);
}

export async function apiDelete<T>(path: string): Promise<T> {
  const response = await fetch(new URL(path, appConfig.apiUrl).toString(), {
    method: "DELETE",
  });
  return handleResponse<T>(response);
}

export function flattenPaginated<T>(raw: PaginatedApiResponse<T>) {
  return {
    items: raw.items,
    total: raw.metadata.total,
    page: raw.metadata.page,
    per_page: raw.metadata.per_page,
    total_pages: raw.metadata.total_pages,
  };
}
