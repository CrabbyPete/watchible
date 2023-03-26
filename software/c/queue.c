
#include <stdlib.h>
#include "pico/stdlib.h"

#include "queue.h"

bool queue_empty(queue_t *queue)
{
    if (queue->tail == queue->head) 
        return true;
    return false;

}

char queue_read(queue_t *queue) {
    if (queue->tail == queue->head) {
        return '\0';
    }
    char handle = queue->data[queue->tail];
    queue->data[queue->tail] = '\0';
    queue->tail = (queue->tail + 1) % queue->size;
    return handle;
}

int queue_write(queue_t *queue, char handle) {
    if (((queue->head + 1) % queue->size) == queue->tail) {
        return -1;
    }
    queue->data[queue->head] = handle;
    queue->head = (queue->head + 1) % queue->size;
    return 0;
}

