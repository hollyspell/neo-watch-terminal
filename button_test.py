from gpiozero import Button
from signal import pause

button = Button(26)
button.when_pressed = lambda: print("Button pressed!")
print("Waiting for presses... Ctrl+C to stop")
pause()
