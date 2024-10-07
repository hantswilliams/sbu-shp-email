# SBU - SHP Email App 

## loca ldev 
### create SSL cert
```bash
openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 365 -nodes
```

## ui 
### example search python code:
```python
output = [msg['id'] for msg in messages if 'research' in msg['snippet'].lower()]
```