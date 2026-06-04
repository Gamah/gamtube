# TODO


## admin panel
some kind of authed view on a separate port that shows hashes, input url's, file size, views(?? consider implications of 
project) and expiry time, have a button to renew expiry (with some option to set it like 100 years in the future to make it 
"permanent" and a delete button also allow differentiating between deleted and unlisting... delete should just clear/expire 
the file, unlist should keep the input URL and never download the file again and retrun a message about "you know what you 
did"
auth can just be a password set in .env endpoing for admin view should be at /manage. put a link to it in a reasonable place
