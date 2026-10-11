#include <stdio.h>

void early_marker(void);

int main(void)
{
    early_marker();
    puts("main reached");
    return 0;
}
