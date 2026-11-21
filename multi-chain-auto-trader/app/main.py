import os, uvicorn
if __name__=='__main__':
    uvicorn.run('app.api:app',host=os.getenv('SIGNAL_API_HOST','127.0.0.1'),port=int(os.getenv('SIGNAL_API_PORT','8787')),log_level='info')
