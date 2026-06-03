# TODO

## S3 storage backend

Implement `S3StorageBackend` in `app/storage/s3.py` (currently all methods raise `NotImplementedError`). Use `boto3`. Should support `save`, `get_url`, `delete`, and return `None` from `get_local_path` (remote backend).
