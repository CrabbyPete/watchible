#include <stdlib.h>

#ifndef __SIMPLE_QUEUE__
#define __SIMPLE_QUEUE__

typedef struct {
    size_t head;
    size_t tail;
    size_t size;
    char *data;
} queue_t;

extern char queue_read(queue_t *queue);
extern int queue_write(queue_t *queue, char handle);
extern bool queue_empty(queue_t *queue);

#endif
