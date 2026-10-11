
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
#else
#include <unistd.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <arpa/inet.h>
#include <netinet/in.h>
#endif

#define PORT 8080
#define MAXLINE 1024

#ifndef _WIN32
#define SOCKET int
#define INVALID_SOCKET (-1)
#define closesocket close
#endif

int main(void)
{
    SOCKET sockfd;
    char buffer[MAXLINE];
    struct sockaddr_in servaddr;
    int len;
    int n;

#ifdef _WIN32
    WSADATA wsaData;
    if (WSAStartup(MAKEWORD(2, 2), &wsaData) != 0)
    {
        perror("WSAStartup failed");
        return EXIT_FAILURE;
    }
#endif

    // Create UDP socket
    sockfd = socket(AF_INET, SOCK_DGRAM, 0);

    if (sockfd == INVALID_SOCKET)
    {
        perror("Socket creation failed");
#ifdef _WIN32
        WSACleanup();
#endif
        exit(EXIT_FAILURE);
    }

    memset(&servaddr, 0, sizeof(servaddr));

    // Configure server address
    servaddr.sin_family = AF_INET;
    servaddr.sin_port = htons(PORT);
    servaddr.sin_addr.s_addr = inet_addr("127.0.0.1");

    // Input string
    printf("Enter a string in uppercase: ");
    if (fgets(buffer, MAXLINE, stdin) == NULL)
    {
        perror("Input failed");
        closesocket(sockfd);
#ifdef _WIN32
        WSACleanup();
#endif
        return EXIT_FAILURE;
    }

    buffer[strcspn(buffer, "\n")] = '\0';

    // Send string to server
    sendto(sockfd, buffer, (int)strlen(buffer), 0,
           (struct sockaddr *)&servaddr, sizeof(servaddr));

    printf("String sent to server.\n");

    // Receive converted string
    len = sizeof(servaddr);

    n = recvfrom(sockfd, buffer, MAXLINE - 1, 0,
                 (struct sockaddr *)&servaddr, &len);

    if (n < 0)
    {
        perror("Receive failed");
        closesocket(sockfd);
#ifdef _WIN32
        WSACleanup();
#endif
        exit(EXIT_FAILURE);
    }

    buffer[n] = '\0';

    printf("Lowercase string from server: %s\n", buffer);

    closesocket(sockfd);
#ifdef _WIN32
    WSACleanup();
#endif

    return 0;
}
