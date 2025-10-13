from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES) # Acquired by youtube
creds = flow.run_local_server(
    host= "host",
    port= port,
    authorization_prompt_message="",
    success_message= "",
)
with open("token.json","w") as f:
    f.write(creds.to_json())