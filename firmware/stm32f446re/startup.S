/* Original minimal Cortex-M4 startup. See repository LICENSE. */
.syntax unified
.cpu cortex-m4
.thumb

.section .isr_vector,"a",%progbits
.balign 512
.global vector_table
.type vector_table,%object
vector_table:
    .word _estack
    .word Reset_Handler
    .word Default_Handler /* NMI */
    .word Default_Handler /* HardFault */
    .word Default_Handler /* MemManage */
    .word Default_Handler /* BusFault */
    .word Default_Handler /* UsageFault */
    .word 0, 0, 0, 0
    .word Default_Handler /* SVCall */
    .word Default_Handler /* Debug monitor */
    .word 0
    .word Default_Handler /* PendSV */
    .word SysTick_Handler /* Exception 15, not external IRQ 15 */
    /* STM32F446 has external IRQ slots 0..96. No peripheral IRQ is enabled. */
    .rept 97
    .word Default_Handler
    .endr
.size vector_table, .-vector_table

.section .text.Reset_Handler,"ax",%progbits
.global Reset_Handler
.type Reset_Handler,%function
.thumb_func
Reset_Handler:
    cpsid i
    ldr r0, =_sidata
    ldr r1, =_sdata
    ldr r2, =_edata
1:
    cmp r1, r2
    bhs 2f
    ldr r3, [r0], #4
    str r3, [r1], #4
    b 1b
2:
    ldr r1, =_sbss
    ldr r2, =_ebss
    movs r3, #0
3:
    cmp r1, r2
    bhs 4f
    str r3, [r1], #4
    b 3b
4:
    bl main
5:
    b 5b
.size Reset_Handler, .-Reset_Handler

.section .text.Default_Handler,"ax",%progbits
.global Default_Handler
.type Default_Handler,%function
.thumb_func
Default_Handler:
    cpsid i
6:
    b 6b
.size Default_Handler, .-Default_Handler

.section .note.GNU-stack,"",%progbits
