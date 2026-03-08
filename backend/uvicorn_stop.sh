#!/bin/bash

PID_FILE="uvicorn.pid"

if [ -f "$PID_FILE" ]; then
    PID=$(cat $PID_FILE)
    kill $PID
    rm $PID_FILE
    echo "Uvicorn stopped."
else
    echo "No PID file found. Is the server running?"
fi