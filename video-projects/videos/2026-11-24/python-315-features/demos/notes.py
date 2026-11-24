with open("notes.txt", "w") as f:      # no encoding= given
    f.write("café ☕  నమస్తే\n")

print(open("notes.txt").read())
