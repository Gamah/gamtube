# TODO

## S3 storage backend
Implement `S3StorageBackend` in `app/storage/s3.py` (currently all methods raise `NotImplementedError`). Use `boto3`. Should support `save`, `get_url`, `delete`, and return `None` from `get_local_path` (remote backend).

## admin panel
some kind of authed view on a separate port that shows hashes, input url's, file size, views(?? consider implications of project) and expiry time, have a button to renew expiry (with some option to set it like 100 years in the future to make it "permanent" and a delete button

